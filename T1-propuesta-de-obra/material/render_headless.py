"""Opcional: produce el render sin abrir el navegador a mano (Chromium headless vía Playwright).
Hace lo mismo que el botón "Render" de index.html: llama a renderDemo() y guarda el WAV.
    pip install playwright && playwright install chromium
    python render_headless.py            -> render/orden-desorden-render.wav
"""
import asyncio
import base64
import os

from playwright.async_api import async_playwright

HERE = os.path.dirname(os.path.abspath(__file__))


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto("file://" + os.path.join(HERE, "index.html"))
        res = await page.evaluate("""async () => {
            const r = await renderDemo(null);
            const u = new Uint8Array(await r.blob.arrayBuffer()); let s = '';
            for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000));
            return {b64: btoa(s), log: r.log, dur: r.buf.duration}; }""")
        os.makedirs(os.path.join(HERE, "render"), exist_ok=True)
        out = os.path.join(HERE, "render", "orden-desorden-render.wav")
        with open(out, "wb") as f:
            f.write(base64.b64decode(res["b64"]))
        with open(os.path.join(HERE, "render", "render-log.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(res["log"]) + "\n")
        print(f"{out}: {res['dur']:.1f} s")
        print("\n".join(res["log"]))
        await browser.close()


asyncio.run(main())
