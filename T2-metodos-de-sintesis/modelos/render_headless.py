"""Produce los audios de los modelos sin abrir el navegador a mano (Chromium/Edge headless vía Playwright).
Hace lo mismo que los botones de index.html, con los valores por defecto (los medidos):
    pip install playwright        (usa el Edge o Chrome instalado; si no hay, `playwright install chromium`)
    python render_headless.py     -> ../audio/golpe-aditiva.wav, ../audio/golpe-modal.wav,
                                     ../audio/parte3-render.wav y ../audio/parte3-render-log.txt
"""
import asyncio
import base64
import os
import pathlib
import sys

from playwright.async_api import async_playwright

sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE.parent / "audio"

JS_B64 = """async (blob) => { const u = new Uint8Array(await blob.arrayBuffer()); let s = '';
  for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000));
  return btoa(s); }"""


async def lanzar(p):
    for canal in ("msedge", "chrome", None):
        try:
            return await p.chromium.launch(channel=canal) if canal else await p.chromium.launch()
        except Exception:
            continue
    raise RuntimeError("No encontré Edge, Chrome ni Chromium de Playwright")


async def main():
    OUT.mkdir(exist_ok=True)
    async with async_playwright() as p:
        browser = await lanzar(p)
        page = await browser.new_page()
        await page.goto(HERE.joinpath("index.html").as_uri())
        await page.evaluate(f"() => {{ window.__b64 = {JS_B64}; }}")
        for modelo, P in (("aditiva", "PARAMS_ADITIVA"), ("modal", "PARAMS_MODAL")):
            b64 = await page.evaluate(
                f"async () => __b64(wavBlob(await renderGolpe('{modelo}', defaults({P}))))")
            (OUT / f"golpe-{modelo}.wav").write_bytes(base64.b64decode(b64))
            print(f"golpe-{modelo}.wav")
        res = await page.evaluate("async () => { const r = await renderParte3(); return {b64: await __b64(r.blob), log: r.log}; }")
        (OUT / "parte3-render.wav").write_bytes(base64.b64decode(res["b64"]))
        (OUT / "parte3-render-log.txt").write_text("\n".join(res["log"]) + "\n", encoding="utf-8")
        print("parte3-render.wav\n" + "\n".join(res["log"]))
        await browser.close()


asyncio.run(main())
