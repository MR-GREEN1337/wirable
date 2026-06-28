import path from "path";
import { fileURLToPath } from "url";
import { createRequire } from "module";
const __dir = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const { chromium } = require(path.resolve(__dir, "../../web/node_modules/playwright"));

const FPS = 30, DURATION = 37500;
const frames = Math.round((DURATION / 1000) * FPS);
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
await page.addInitScript(() => { window.__CAPTURE__ = true; });
await page.goto("file://" + path.resolve(__dir, "reel.html"));
await page.evaluate(() => document.fonts.ready);
await page.waitForTimeout(800); // let fonts + images settle

for (let f = 0; f < frames; f++) {
  const ms = (f / FPS) * 1000;
  await page.evaluate((m) => window.seek(m), ms);
  await page.screenshot({ path: path.resolve(__dir, `frames/f${String(f).padStart(4, "0")}.png`) });
  if (f % 60 === 0) console.log(`frame ${f}/${frames}`);
}
await browser.close();
console.log("done", frames, "frames");
