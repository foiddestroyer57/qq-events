"""Shrinks a brochure (PDF or image) to under a size limit so Gemini can read it.
Only the copy that is sent to Gemini is shrunk - the original file in Drive is never changed."""
import os, io, shutil, tempfile, subprocess

class TooBig(Exception):
    """The file could not be brought under the limit."""

def shrink(data, mime, limit):
    """Returns (data, mime). Files already under the limit are returned untouched."""
    if len(data) <= limit: return data, mime
    if mime == "application/pdf": return _pdf(data, limit), mime
    if mime.startswith("image/"): return _image(data, limit)
    raise TooBig("cannot shrink files of type %s" % mime)

def _pdf(data, limit):
    gs = shutil.which("gs")
    if not gs: raise TooBig("Ghostscript is not installed on the runner")
    smallest = len(data)
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "in.pdf")
        with open(src, "wb") as f: f.write(data)
        for i, (preset, dpi) in enumerate([("/ebook", 150), ("/screen", 100), ("/screen", 72), ("/screen", 50)]):
            out = os.path.join(d, "out%d.pdf" % i)
            cmd = [gs, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5", "-dNOPAUSE", "-dBATCH", "-dQUIET", "-dSAFER",
                   "-dPDFSETTINGS=" + preset,
                   "-dDownsampleColorImages=true", "-dColorImageResolution=%d" % dpi,
                   "-dDownsampleGrayImages=true", "-dGrayImageResolution=%d" % dpi,
                   "-dDownsampleMonoImages=true", "-dMonoImageResolution=%d" % dpi,
                   "-sOutputFile=" + out, src]
            try: subprocess.run(cmd, check=True, timeout=300, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            except subprocess.CalledProcessError as e: raise TooBig("Ghostscript failed: " + e.stderr.decode("utf-8", "replace")[:150])
            except subprocess.TimeoutExpired: raise TooBig("Ghostscript took too long")
            size = os.path.getsize(out); smallest = min(smallest, size)
            if 0 < size <= limit:
                with open(out, "rb") as f: return f.read()
    raise TooBig("still %.1f MB after the strongest compression" % (smallest / 1e6))

def _image(data, limit):
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data)); img.load()
    except Exception as e: raise TooBig("could not open the image: %s" % str(e)[:100])
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA"); bg = Image.new("RGB", img.size, "white"); bg.paste(img, mask=img.split()[-1]); img = bg
    else: img = img.convert("RGB")
    for scale in (1.0, 0.8, 0.6, 0.45, 0.3):
        im = img if scale == 1.0 else img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
        for q in (85, 70, 55):
            buf = io.BytesIO(); im.save(buf, "JPEG", quality=q, optimize=True)
            if buf.tell() <= limit: return buf.getvalue(), "image/jpeg"
    raise TooBig("image is still over the limit after shrinking")
