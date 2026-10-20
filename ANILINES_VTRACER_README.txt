AniLines + VTracer JSXGraph Generator
=====================================

This is an isolated JSXGraph/JSXG generator. It does not launch Desmos or modify
the original Anime to Desmos Studio project.

Tracing workflow
----------------
Each selected video frame is processed by AniLines Detail. VTracer converts the
line-art mask to cubic spline paths. Those paths are sampled and displayed as
JSXGraph curves. Frame data is exported as JSXGraph animation JSON, and the CPU
renderer creates a matching MP4 preview.

Start
-----
1. Run Setup-Version.bat once to install this folder's private Python environment.
2. Run Run-JSXG-Generator.bat.
3. Choose the source video, clip duration, and frame rate.
4. Click Use source FPS to process at the original clip frame rate.
5. Click Generate JSXGraph animation. The JSXGraph player opens after generation;
   select the generated JSXGraph animation JSON in its file control.

Curve allocation is automatic per frame: all eligible VTracer curves are kept
for the current frame. A frame may have fewer or more curves than its neighbors;
there is no 2,400-curve setting or shared curve allowance across frames. A frame
with no detected curves is saved as an empty frame and processing continues.

Files are saved under output\<timestamp> for animation JSON and traced frames,
and output mp4\<time-date>.mp4 for CPU preview video.

The JSXGraph HTML player downloads JSXGraph from its CDN when opened, so it needs
an internet connection. The MP4 render itself is performed locally on CPU.
