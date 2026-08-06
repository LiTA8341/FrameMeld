import vapoursynth as vs

core = vs.core
required = ("akarin", "bs", "lsmas", "mv", "rife", "svp1", "svp2", "adjust")
available = {plugin.namespace for plugin in core.plugins()}
missing = [name for name in required if name not in available]
if missing:
    raise RuntimeError("missing VapourSynth plugin namespace(s): " + ", ".join(missing))

clip = core.std.BlankClip(width=64, height=64, format=vs.YUV420P8, length=2, fpsnum=30)
clip.set_output()
