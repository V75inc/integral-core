// Render the current split-square master directly from its geometry.
// Set NODE_PATH to a runtime containing sharp before running this script.
const fs = require('node:fs');
const path = require('node:path');
const sharp = require('sharp');
const g = require('./geometry.json');
const root = __dirname;
const sizes = [16, 24, 32, 48, 64, 96, 128, 180, 192, 256, 384, 512, 1024, 2048];
function svg(color, background, padding = 0) {
  const extent = 604 + padding * 2;
  const origin = 58 - padding;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${extent}" height="${extent}" viewBox="${origin} ${origin} ${extent} ${extent}">${background ? `<rect x="${origin}" y="${origin}" width="${extent}" height="${extent}" fill="${background}"/>` : ''}<path d="${g.halfPath}" transform="${g.leftTransform}" fill="${color}"/><path d="${g.halfPath}" transform="${g.rightTransform}" fill="${color}"/></svg>`;
}

async function render(name, source, size) {
  const file = path.join(root, name);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  await sharp(Buffer.from(source)).resize(size, size).png().toFile(file);
  const info = await sharp(file).metadata();
  if (info.width !== size || info.height !== size) throw new Error(`Bad dimensions: ${name}`);
}
(async () => {
  for (const [name,color] of [['black','#000'],['white','#fff']]) {
    const source = svg(color);
    fs.writeFileSync(path.join(root, `integral-logo${name === 'white' ? '-white' : ''}.svg`), source);
    for (const size of sizes) await render(`png/${name}/integral-logo-${size}.png`, source, size);
  }
  for (const size of [192,512,1024]) {
    await render(`png/on-white/integral-logo-${size}.png`, svg('#000','#fff',72), size);
    await render(`png/on-black/integral-logo-${size}.png`, svg('#fff','#000',72), size);
  }
  for (const [name,size,padding] of [['favicon-16.png',16,0],['favicon-32.png',32,0],['favicon.png',512,0],['apple-touch-icon.png',180,72],['android-chrome-192.png',192,72],['android-chrome-512.png',512,72]]) {
    await render(`icons/${name}`, svg('#000', padding ? '#fff' : null, padding), size);
  }
  await render('preview.png',svg('#000','#fff',72),1024);
  console.log('Rendered 41 PNG assets and 2 transparent SVG masters; dimensions verified.');
})().catch(e => { console.error(e); process.exit(1); });
