// Opt-in, paid live-provider QA. Run from a Mac against a disposable loopback workspace.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';

const env = process.env;
const origin = new URL(env.VOICE_QA_URL || 'http://127.0.0.1:19218').origin;
assert.equal(new URL(origin).hostname, '127.0.0.1');
assert.equal(env.VOICE_QA_LIVE, '1', 'Set VOICE_QA_LIVE=1 to place one synthetic call with the configured provider.');
for (const key of ['VOICE_QA_EMAIL', 'VOICE_QA_PASSWORD', 'VOICE_QA_WORKSPACE', 'VOICE_QA_WHISPER_MODEL']) {
  assert.ok(env[key], `Missing ${key}`);
}
const { chromium } = createRequire(import.meta.url)(env.PLAYWRIGHT_PACKAGE_PATH || 'playwright');
const dir = await mkdtemp(path.join(tmpdir(), 'integral-voice-journey-'));
// Supply the authorized first page in UI order to verify what the caller heard.
const expectedApps = env.VOICE_QA_APP_NAMES ? JSON.parse(env.VOICE_QA_APP_NAMES) : null;
if (expectedApps !== null) assert.ok(Array.isArray(expectedApps) && expectedApps.every(name => typeof name === 'string' && name.trim()));
const normalizeSpeech = value => value.toLowerCase().normalize('NFKC').replaceAll('&', 'and').replace(/[^\p{L}\p{N}]/gu, '');
const heldOut = process.argv.includes('--variant');
const note = heldOut ? 'water the green fern' : 'check the blue umbrella';
const postList = process.argv.includes('--post-list');
if (postList) assert.ok(expectedApps?.length, 'Supply the expected directory names for the chat write check.');
const cases = postList ? [
  ['apps', heldOut ? 'What apps do I have in here?' : 'Which apps can I use here?'],
  ['post-list', heldOut ? 'Put those in our chat so I can look at them.' : 'Can you list it out in the chat?'],
] : [
  ['orientation', heldOut ? 'How can you help me here, in a sentence?' : 'What can you help me do here? One sentence, please.'],
  ['workspace', heldOut ? 'Tell me just the name of this workspace.' : 'Which workspace am I in? Just the name.'],
  ['apps', heldOut ? 'What apps have I got in this workspace?' : 'Which apps can I use here?'],
  ['repeat-apps', 'Could you say those app names once more?'],
  ['save-note', heldOut ? `Leave this note in our chat: ${note}.` : `Put a short note in our chat that says ${note}.`],
  ['recall', heldOut ? 'Read that note back to me.' : 'What did the note I just left say?'],
];
const browser = await chromium.launch({
  executablePath: env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream', '--mute-audio', '--autoplay-policy=no-user-gesture-required'],
});
const results = [];
let savedOnReload = false;
let visibleDuringCall = false;
let boundThread;
try {
  const context = await browser.newContext({ permissions: ['microphone'] });
  await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
  const page = await context.newPage();
  page.on('websocket', socket => socket.on('framereceived', ({payload}) => {
    if (typeof payload !== 'string' || !payload.startsWith('{')) return;
    const frame = JSON.parse(payload);
    if (frame.type === 'ready' && typeof frame.thread_id === 'string') boundThread = frame.thread_id;
  }));
  await page.goto(origin + '/login');
  await page.locator('input[type=email]').fill(env.VOICE_QA_EMAIL);
  await page.locator('input[type=password]').fill(env.VOICE_QA_PASSWORD);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  const workspace = page.getByRole('button', { name: /Switch workspace/ });
  await workspace.waitFor();
  if (!(await workspace.getAttribute('aria-label')).includes(env.VOICE_QA_WORKSPACE)) {
    await workspace.click();
    await page.getByRole('option').filter({ hasText: env.VOICE_QA_WORKSPACE }).click();
  }
  const selected = page.getByRole('button', { name: 'Switch workspace (currently ' + env.VOICE_QA_WORKSPACE + ')', exact: true });
  await selected.waitFor();
  await page.goto(origin + '/agent');
  await selected.waitFor();
  const fresh = page.getByRole('button', { name: 'New conversation', exact: true });
  const show = page.getByRole('button', { name: 'Show conversations', exact: true });
  if (!await fresh.isVisible() && !await show.isVisible()) {
    const chat = page.getByRole('button', { name: /^(open chat|open assistant|chat)$/i }).first();
    if (await chat.isVisible()) await chat.click();
  }
  await Promise.race([fresh.waitFor(), show.waitFor()]);
  if (!await fresh.isVisible()) await show.click();
  await fresh.click();
  const start = page.getByRole('button', { name: 'Start voice call', exact: true });
  const end = page.getByRole('button', { name: 'End call', exact: true });
  await start.waitFor();

  // The only microphone is synthetic. Observe the app's scheduled speaker output.
  await page.evaluate(() => {
    const state = window.voiceJourney = { playing: 0, last: performance.now(), chunks: [], first: null, recording: false };
    navigator.mediaDevices.getUserMedia = async () => {
      const audio = new AudioContext({ sampleRate: 24000 });
      await audio.resume();
      const mic = audio.createMediaStreamDestination();
      state.say = async encoded => {
        const source = audio.createBufferSource();
        source.buffer = await audio.decodeAudioData(Uint8Array.from(atob(encoded), c => c.charCodeAt(0)).buffer);
        source.connect(mic);
        state.inputEnd = performance.now() + source.buffer.duration * 1000;
        state.chunks = [];
        state.first = null;
        state.recording = true;
        source.start();
      };
      return mic.stream;
    };
    const connect = AudioNode.prototype.connect;
    const speakers = new WeakSet();
    AudioNode.prototype.connect = function(destination, ...args) {
      if (this instanceof AudioBufferSourceNode && destination instanceof AudioDestinationNode) speakers.add(this);
      return connect.call(this, destination, ...args);
    };
    const schedule = AudioBufferSourceNode.prototype.start;
    AudioBufferSourceNode.prototype.start = function(when = 0, ...rest) {
      if (speakers.has(this)) {
        state.playing++;
        this.addEventListener('ended', () => { state.playing--; state.last = performance.now(); }, { once: true });
        if (state.recording) {
          state.first ??= performance.now() + 1000 * Math.max(0, when - this.context.currentTime);
          if (this.buffer.sampleRate !== 24000) throw Error('Unexpected voice playback sample rate');
          const pcm = Int16Array.from(this.buffer.getChannelData(0), x => Math.max(-32768, Math.min(32767, Math.round(x * 32768))));
          const bytes = new Uint8Array(pcm.buffer);
          let binary = '';
          for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
          state.chunks.push(btoa(binary));
        }
      }
      return schedule.call(this, when, ...rest);
    };
  });
  await start.click();
  try {
    await end.waitFor({ timeout: 25000 });
    await page.waitForFunction(() => window.voiceJourney.say);
    // Allow the opening greeting to settle before the first caller turn.
    await page.waitForTimeout(4000);
    for (const [id, prompt] of cases) {
      const base = path.join(dir, id);
      execFileSync('say', ['-v', 'Samantha', '-r', '165', '-o', base + '.aiff', prompt]);
      execFileSync('afconvert', ['-f', 'WAVE', '-d', 'LEI16@24000', base + '.aiff', base + '-in.wav']);
      await page.waitForFunction(() => !window.voiceJourney.playing && performance.now() - window.voiceJourney.last > 1500);
      await page.evaluate(wav => window.voiceJourney.say(wav), (await readFile(base + '-in.wav')).toString('base64'));
      await page.waitForFunction(() => {
        const s = window.voiceJourney;
        return s.first !== null && !s.playing && performance.now() - s.last > 2400;
      }, null, { timeout: 60000 });
      const capture = await page.evaluate(() => {
        const s = window.voiceJourney;
        s.recording = false;
        return { chunks: s.chunks, firstPlaybackMs: Math.round(s.first - s.inputEnd), lastPlaybackMs: Math.round(s.last - s.inputEnd) };
      });
      await writeFile(base + '.pcm', Buffer.concat(capture.chunks.map(chunk => Buffer.from(chunk, 'base64'))));
      execFileSync('ffmpeg', ['-loglevel','error','-f','s16le','-ar','24000','-ac','1','-i',base+'.pcm',base+'.wav']);
      results.push({ id, firstPlaybackMs: capture.firstPlaybackMs, lastPlaybackMs: capture.lastPlaybackMs });
      if (id === 'post-list') {
        const lastName = page.locator('.aui-md li').filter({hasText: expectedApps.at(-1)}).last();
        visibleDuringCall = await lastName.waitFor({timeout: 10000}).then(() => end.isVisible(), () => false);
      }
    }
  } finally {
    if (await end.isVisible()) await end.click();
  }
  await start.waitFor();
  assert.ok(boundThread, 'Missing the admitted conversation');
  await page.goto(origin + '/agent?thread=' + encodeURIComponent(boundThread));
  const readThread = page.waitForResponse(r => new URL(r.url()).pathname === '/api/chat/threads/' + boundThread && r.request().method() === 'GET' && r.ok());
  await page.reload();
  const {messages} = await (await readThread).json();
  const notes = messages.filter(m => m.role === 'assistant' && m.provider_metadata?.source === 'voice_note');
  if (postList) {
    const text = notes[0]?.parts.find(p => p.type === 'text')?.text ?? '';
    const lines = text.split('\n').filter(line => /^\d+\. /.test(line)).map(line => line.replace(/^\d+\. /, ''));
    savedOnReload = notes.length === 1 && JSON.stringify(lines) === JSON.stringify(expectedApps)
      && await page.locator('.aui-md li').filter({hasText: expectedApps.at(-1)}).last().waitFor({timeout: 5000}).then(() => true, () => false);
  } else {
    const saved = notes.filter(m => m.parts.some(p => p.type === 'text' && p.text.toLowerCase().includes(note)));
    savedOnReload = saved.length === 1 && await page.getByText(note, { exact: false }).first().waitFor({ timeout: 20000 }).then(() => true, () => false);
  }
} finally {
  await browser.close();
}
// Use an existing local model file. This evaluation never calls a hosted transcriber.
execFileSync('whisper', [...results.map(r => path.join(dir, r.id + '.wav')), '--model', env.VOICE_QA_WHISPER_MODEL,
  '--language', 'en', '--fp16', 'False', '--output_format', 'txt', '--output_dir', dir], { stdio: 'ignore' });
for (const result of results) {
  result.heard = (await readFile(path.join(dir, result.id + '.txt'), 'utf8')).trim().replace(/\s+/g, ' ');
  const words = result.heard.toLowerCase().replace(/[^a-z0-9 ]/g, '').split(/\s+/);
  result.passed = words.length > 1 && result.firstPlaybackMs >= 0;
  const announcesAction = /\b(?:let me (?!know\b)|I(?:['’]ll| will|['’]m going to)\s)/i;
  result.unnecessaryPreamble = ['workspace', 'apps', 'repeat-apps', 'save-note', 'recall'].includes(result.id) && announcesAction.test(result.heard);
  if (result.id === 'recall') {
    const spoken = words.join(' ').trim();
    const noteIndex = spoken.lastIndexOf(note);
    result.addedWordsAfterNote = noteIndex >= 0 && Boolean(spoken.slice(noteIndex + note.length).trim());
  }
  if (result.id === 'apps' || result.id === 'repeat-apps') {
    result.readToolHeading = /\bvisible apps here\b/i.test(result.heard);
    result.passed &&= !result.readToolHeading;
    if (expectedApps?.length) {
      const heard = normalizeSpeech(result.heard);
      let cursor = 0;
      result.allNamesInOrder = expectedApps.every(name => {
        const label = normalizeSpeech(name);
        const found = heard.indexOf(label, cursor);
        if (found < 0) return false;
        cursor = found + label.length;
        return true;
      });
      // Chat-post qualification checks exact names in the saved message;
      // audio-only qualification also requires an exact offline transcription.
      if (!postList) result.passed &&= result.allNamesInOrder;
    }
  }
  if (result.id === 'post-list') {
    result.repeatedNames = expectedApps.filter(name => normalizeSpeech(result.heard).includes(normalizeSpeech(name)));
    result.passed &&= result.repeatedNames.length === 0 && /chat|conversation|post|wrote|written|put|saved|added/i.test(result.heard);
  }
  if (result.id === 'orientation') result.passed &&= /help|work|task|answer|chat/i.test(result.heard);
  if (result.id === 'save-note') result.passed &&= /sav|add|post|wrote|written|left/i.test(result.heard);
  if (result.id === 'workspace') result.passed &&= normalizeSpeech(result.heard) === normalizeSpeech(env.VOICE_QA_WORKSPACE);
  if (result.id === 'recall') result.passed &&= note.split(' ').every(w => words.includes(w)) && !/pop.?up|can(?:not|'t)|unable|not able|don't have|didn't save|couldn't/i.test(result.heard);
}
await writeFile(path.join(dir, 'results.json'), JSON.stringify(results, null, 2));
console.log(dir, { savedOnReload, visibleDuringCall, results });
if (postList) assert.ok(visibleDuringCall, 'No written list appeared while the call was active');
assert.ok(savedOnReload, 'Saved note missing on reload of the admitted thread');
assert.ok(results.every(r => r.passed), 'Review the captured playback and saved chat before qualifying this iteration.');
