const messages = document.querySelector('#messages');
const empty = document.querySelector('#empty');
const status = document.querySelector('#status');
const input = document.querySelector('#message');
const send = document.querySelector('#send');
const restart = document.querySelector('#restart');
const root = '/public-web-chat-preview';
let conversation = null;

async function request(path, body) {
  const response = await fetch(root + path, body === undefined ? undefined : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(result.error);
    error.status = response.status;
    throw error;
  }
  return result;
}

function render({ conversation: current, messages: history }) {
  if (current !== conversation) messages.replaceChildren();
  conversation = current;
  for (const message of history.slice(messages.childElementCount)) {
    const item = document.createElement('li');
    item.dataset.role = message.role;
    const author = document.createElement('strong');
    author.textContent = message.role === 'visitor' ? 'You' : 'Preview assistant';
    item.append(author, document.createTextNode(message.text));
    messages.append(item);
  }
  empty.hidden = history.length > 0;
  messages.scrollTop = messages.scrollHeight;
}

async function run(action, { focus = false } = {}) {
  input.disabled = true;
  send.disabled = true;
  restart.disabled = true;
  status.textContent = '';
  try {
    render(await action());
  } catch (error) {
    if ([401, 404, 412].includes(error.status)) {
      render({ conversation: null, messages: [] });
    }
    status.textContent = error.status ? error.message : 'The preview could not respond. Try again.';
  } finally {
    input.disabled = !conversation;
    send.disabled = !conversation;
    restart.disabled = false;
    if (focus && conversation) input.focus();
  }
}

document.querySelector('#composer').addEventListener('submit', event => {
  event.preventDefault();
  if (send.disabled || !input.value.trim()) return;
  void run(async () => {
    const history = await request('/message', { conversation, message: input.value });
    input.value = '';
    return history;
  }, { focus: true });
});

restart.addEventListener('click', () => {
  void run(async () => {
    const history = await request('/session', {});
    input.value = '';
    return history;
  }, { focus: true });
});

void run(async () => {
  try {
    return await request('/session');
  } catch (error) {
    if (error.status !== 401) throw error;
    return request('/session', {});
  }
});
