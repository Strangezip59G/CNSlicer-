# Anime-to-Desmos Agent (prototype)

A local Python/OpenCV prototype that extracts frames from a downloaded anime clip, blends local source colors across a triangular mesh, and traces contours, and sends the resulting parametric line segments to an embedded Desmos calculator.

## Native Windows desktop dashboard

The desktop dashboard offers two animation styles: **Color** (triangle fills with smooth colored outlines) and **Sketch** (monochrome contours). Sketch mode uses VTracer's native cubic-spline vectorizer to produce smooth Bézier curves, then assigns those curves to anime-part regions and spatial tiles so busy sections cannot consume the whole expression budget. The anime parser runs locally on the CPU. On the first sketch build it downloads the Mask2Former v3 weights (about 432 MB) into the app's `model cache` folder; later builds reuse them. This extra analysis makes sketch tracing slower, and the status line announces the first download. Pixel cues cannot reliably identify every semantic object: smoke, tree trunks, jewelry, and a particular character's dress are approximated from the parser class, color, and shape. VTracer paths are converted into Desmos cubic parametric expressions; OpenCV tracing remains as a fallback. Video extraction and tracing run in a background worker with progress shown in the window. Use **Render MP4 on CPU** to rasterize the saved JSXGraph polygons and curves locally with OpenCV. It renders frames in up to four CPU worker processes, uses 2× supersampling for antialiased output, and keeps the generated equation JSON paired with the MP4. The JSXGraph app window remains available to inspect and play the equations. Sketch mode also retains **Full-FPS Sketch MP4**, which captures every source frame through the Desmos API and can take longer.

From the project folder in VS Code, start it with:

```powershell
.\.venv\Scripts\python.exe desktop_app.py
```

To build a clickable Windows desktop app, double-click `Build-Desktop-Exe.bat`. It creates `dist\Anime to Desmos Studio\Anime to Desmos Studio.exe`; enter the Desmos key in the `api keys.txt` file beside the executable. The JSXGraph renderer opens in an app-style window and uses the installed Edge or Chrome rendering engine.

Choose a video, extract frames, select Color or Sketch, then build the animation. In the graph window, use **Play** or drag the `f` slider. **Record MP4** captures each sampled Desmos frame and saves the result in that job's folder under `outputs`. The animation JSON is saved alongside it. For smooth source-rate sketch output, choose Sketch mode and click **Full-FPS Sketch MP4**; in the opened Desmos window, click **Render full-FPS sketch**. Stop is available to save a partial render. The Desmos API key is read from the `DESMOS_API_KEY` environment variable; it is not written to the output files.

Color mode uses more graph expressions. Start with a 28–32 px triangle size and 1–2 sampled frames per second; use a smaller triangle size for more color detail if your computer handles the graph well. Sketch mode keeps source frames at up to 1920 px and automatically adapts edge sensitivity per frame using luminance, color contrast, and edge density. It then assigns separate curve-fitting profiles to the parser's semantic regions. Its 2,400-segment default can be raised to 5,000, at the cost of larger equation files and slower Desmos playback. Rebuild the animation after changing quality settings; previously saved JSON/MP4 files keep their original detail.

## What works in this MVP
- Upload a local video (MP4/MOV/MKV/AVI/WEBM).
- Extract frames from up to 20 seconds, sampled at 0.25–120 frames/second (the desktop extractor can use the source FPS).
- Select a frame to trace.
- Smooth textured areas while preserving strong edges, group colors in perceptual Lab space, clean isolated pixels, and convert contours to adjustable cubic curves.
- Generate Desmos parametric expressions and load them into an embedded Desmos calculator when an API key is configured.
- Download the expression JSON.

## Important limitations
This is a proof of concept, not a complete anime-to-vector system. It currently traces one selected frame at a time. It draws simplified filled color regions with contour strokes; complex scenes can generate many expressions. Temporal character tracking, automatic scene understanding, region filling, and full animation export are future phases.

## Requirements
- Windows 10/11
- Python 3.10 or newer (Python 3.10 or 3.11 recommended)
- A Desmos API key for the embedded calculator. Request one through the Desmos API documentation: https://www.desmos.com/api/v1.12/docs/index.html
- A downloaded video clip you have permission to process. Start with a 10–20 second clip.

## Setup (Windows PowerShell)
```powershell
cd anime_to_desmos_agent
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
$env:DESMOS_API_KEY="YOUR_DESMOS_API_KEY"
python app.py
```
Then open http://127.0.0.1:5000 in your browser.

If PowerShell blocks activation, run:
```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
```

## How to use
1. Open the local app.
2. Select a video file.
3. Choose a clip duration up to 20 seconds and a sample FPS (2 FPS is a reasonable start).
4. Upload and wait for the thumbnails.
5. Select a frame.
6. For detail, start with 16 outline palette colors, image smoothing 2, curve smoothing 6, triangle size 18 px, and 800 max outline segments. Triangle fills blend source pixels locally for a much wider range of shades. Smaller triangles capture more local color changes but create many Desmos expressions and can slow the graph. The default 18 px mesh is a faster starting point.
7. Click **Trace selected frame**.
8. Inspect the graph and download `desmos_expressions.json`.


## Monochrome Desmos animation mode
After uploading a clip, click **Build sketch animation**. Python traces each sampled frame with automatic luminance and color-channel edge detection; the page adds a Desmos slider named f that selects each frame, so you can drag or play f to move through the line drawings; the animation JSON can also be downloaded. Sensitivity adjusts to each frame without a manual control. This mode is outline-only and keeps one frame's expressions active at a time. The default 2,400 segments per frame preserves more contours than the previous 1,200 cap. Use 1 to 2 sampled frames per second for a responsive preview; higher frame rates improve motion smoothness but increase build and playback work.

## Troubleshooting
- **Video will not open:** convert it to H.264 MP4 and retry.
- **Graph is slow:** increase triangle size, reduce max segments, or lower palette size.
- **No embedded graph:** confirm `DESMOS_API_KEY` was set in the same PowerShell window before launching Python.
- **Contours look messy:** try a simpler frame with a plain background and lower the number of colors.

## Roadmap
1. Character segmentation and background separation.
2. Temporal tracking and shared motion variables.
3. Multi-frame interpolation and animation timeline.
4. Desmos state export/import and project saving.

## Reference workflow
The Desmos playback page now requests frame equations as the `f` slider moves rather than embedding every frame in one large page. This adapts the on-demand expression workflow from [DesmosBezierRenderer](https://github.com/kevinjycui/DesmosBezierRenderer). Sketch vectorization uses [VTracer](https://github.com/visioncortex/vtracer)'s Windows-compatible native library and spline fitting; the repository's web app and other unused components are not included in this project.



## DesmosPlayer export

After building an animation, the job folder contains `desmos_player_program.js` and the matching `desmos_sketch_animation.json` or `desmos_color_animation.json`. For older animation JSON files, use **Create DesmosPlayer loader** in the desktop app to generate a matching loader without tracing again. The small JavaScript loader is for [DesmosPlayer](https://github.com/jason-woolf/DesmosPlayer), which runs as a browser userscript on Desmos.com. In Chrome or Edge, install and enable the userscript, open the Desmos Graphing Calculator, choose **+ → Program**, paste the loader into the Program expression, and click its round icon. When prompted, select the matching animation JSON from the same job folder. Then click DesmosPlayer's **Start** button. It creates the frame equations, updates them in grouped steps, and advances `f` as a frame counter.

The animation data stays in the JSON file rather than being pasted into a huge Program expression. DesmosPlayer is browser-only and needs access to the local JSON file when the loader runs.
