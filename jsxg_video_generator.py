from __future__ import annotations

import json
import os
import threading
import time
import webbrowser
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from types import SimpleNamespace
import sys

import cv2
import numpy as np
import torch
ROOT = Path(__file__).resolve().parent
REPO = ROOT / "AniLines-source"
sys.path.insert(0, str(REPO))
from infer import inference, load_model

import processor
from cpu_renderer import render_animation_to_mp4


OUTPUT = ROOT / "output"
OUTPUT.mkdir(exist_ok=True)
MP4_OUTPUT = ROOT / "output mp4"
MP4_OUTPUT.mkdir(exist_ok=True)


def create_jsxgraph_player() -> str:
    return '''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AniLines + VTracer · JSXGraph</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/jsxgraph@1.14.0/distrib/jsxgraph.css"><script src="https://cdn.jsdelivr.net/npm/jsxgraph@1.14.0/distrib/jsxgraphcore.js"></script>
<style>html,body{margin:0;height:100%;font:14px Segoe UI,Arial;color:#17212b}body{display:flex;flex-direction:column}header{display:flex;gap:10px;align-items:center;padding:10px 14px;background:#182230;color:white;flex-wrap:wrap}button,input{font:inherit}main{display:flex;min-height:0;flex:1}.side{width:270px;overflow:auto;border-right:1px solid #ccd3dc;padding:10px}.stage{position:relative;flex:1;min-width:0}.jxgbox{width:100%;height:100%;border:0}.muted{color:#657386}#error{color:#a00}</style></head><body>
<header><b>JSXGraph</b><span>AniLines Detail → VTracer spline animation</span><input id="file" type="file" accept=".json,application/json"><button id="play" disabled>Play</button><button id="fullscreen">Full screen</button><input id="timeline" type="range" min="0" max="0" value="0" disabled><span id="clock">Choose animation JSON</span></header>
<main><aside class="side"><b>Curves</b><div id="equations" class="muted">Select the generated JSXGraph animation JSON.</div></aside><section class="stage"><div id="jxgbox" class="jxgbox"></div><div id="error"></div></section></main>
<script>
const input=document.getElementById('file'),play=document.getElementById('play'),timeline=document.getElementById('timeline'),clock=document.getElementById('clock'),list=document.getElementById('equations'),error=document.getElementById('error');
let data=null,objects=[],index=0,timer=0,fps=1;
const board=window.JXG?JXG.JSXGraph.initBoard('jxgbox',{boundingbox:[0,1,1,0],axis:true,grid:true,keepaspectratio:true,showNavigation:false,showCopyright:false,renderer:'canvas',pan:{enabled:false},zoom:{enabled:false}}):null;
input.addEventListener('change',async()=>{try{data=JSON.parse(await input.files[0].text());if(!Array.isArray(data.frames)||!data.frames.length)throw new Error('The selected JSON has no frames.');fps=Math.max(.1,Number(data.sample_fps)||1);timeline.max=String(data.frames.length-1);timeline.disabled=false;play.disabled=false;render(0);}catch(e){error.textContent=e.message;}});
function render(i){if(!data||!board)return;index=Math.max(0,Math.min(data.frames.length-1,i));timeline.value=String(index);clock.textContent=`Frame ${index+1}/${data.frames.length} · ${fps.toFixed(2)} FPS`;const frame=data.frames[index],curves=frame.curves||[];board.suspendUpdate();try{if(objects.length)board.removeObject(objects,false);objects=[];const b=frame.graph_bounds||{left:0,right:frame.image_width||1,bottom:0,top:frame.image_height||1};board.setBoundingBox([b.left,b.top,b.right,b.bottom],false);for(const c of curves){const p=c.geometry?.points||[];if(p.length>1)objects.push(board.create('curve',[p.map(q=>q[0]),p.map(q=>q[1])],{strokeColor:c.color||'#111',strokeWidth:c.lineWidth||1,highlight:false}));}}finally{board.unsuspendUpdate();}list.innerHTML=curves.slice(0,250).map((c,n)=>`<div class="muted">Curve ${n+1} · ${(c.geometry?.points||[]).length} spline samples</div>`).join('')+(curves.length>250?`<div class="muted">… ${curves.length-250} more curves</div>`:'');}
timeline.addEventListener('input',()=>render(Number(timeline.value)));play.addEventListener('click',()=>{if(timer){clearInterval(timer);timer=0;play.textContent='Play';return;}play.textContent='Pause';timer=setInterval(()=>{if(index>=data.frames.length-1){clearInterval(timer);timer=0;play.textContent='Play';return;}render(index+1);},1000/fps);});document.getElementById('fullscreen').onclick=()=>document.documentElement.requestFullscreen?.();
</script></body></html>'''


class Generator(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AniLines + VTracer · JSXGraph Generator")
        self.geometry("800x580")
        self.minsize(700, 500)
        self.configure(bg="#eef2f7")
        self.video = tk.StringVar()
        self.source_fps = None
        self.player_path = None
        self.seconds = tk.StringVar(value="5")
        self.fps = tk.StringVar(value="1")
        self.status = tk.StringVar(value="Choose a clip to start.")
        self.running = False
        self._build()

    def _build(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#eef2f7")
        style.configure("TLabel", background="#eef2f7", foreground="#182230", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 20))
        style.configure("TButton", font=("Segoe UI Semibold", 10), padding=8)
        outer = ttk.Frame(self, padding=22); outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="AniLines + VTracer", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Video frames → AniLines detail tracing → VTracer splines → JSXGraph animation").pack(anchor="w", pady=(4, 18))
        card = ttk.Frame(outer, padding=16); card.pack(fill="x")
        ttk.Label(card, text="Input video").grid(row=0,column=0,sticky="w")
        ttk.Entry(card, textvariable=self.video).grid(row=1,column=0,sticky="ew",pady=(5,14))
        ttk.Button(card,text="Browse…",command=self.choose).grid(row=1,column=1,padx=(10,0),pady=(5,14))
        controls=ttk.Frame(card); controls.grid(row=2,column=0,columnspan=2,sticky="ew")
        for col,(label,var,width) in enumerate((("Clip length (sec)",self.seconds,12),("Frames per second",self.fps,12))):
            box=ttk.Frame(controls);box.grid(row=0,column=col,sticky="w",padx=(0,18))
            ttk.Label(box,text=label).pack(anchor="w")
            if label=="Frames per second":
                row=ttk.Frame(box);row.pack(anchor="w",pady=(5,0));ttk.Entry(row,textvariable=var,width=width).pack(side="left");ttk.Button(row,text="Use source FPS",command=self.use_source_fps).pack(side="left",padx=(6,0))
            else:ttk.Entry(box,textvariable=var,width=width).pack(anchor="w",pady=(5,0))
        self.start_button=ttk.Button(outer,text="Generate JSXGraph animation",command=self.start);self.start_button.pack(anchor="w",pady=16)
        self.open_player_button=ttk.Button(outer,text="Open JSXGraph player",command=self.open_player,state="disabled");self.open_player_button.pack(anchor="w",pady=(0,10))
        self.progress=ttk.Progressbar(outer,mode="determinate");self.progress.pack(fill="x")
        ttk.Label(outer,textvariable=self.status).pack(anchor="w",pady=(8,10))
        self.log=tk.Text(outer,height=14,wrap="word",bg="#101923",fg="#d9e4f0",insertbackground="white",font=("Cascadia Mono",9),relief="flat",padx=10,pady=8)
        self.log.pack(fill="both",expand=True)
        self.log.insert("end","Selected workflow: AniLines Detail raster followed by VTracer cubic spline tracing.\nCurve counts are detected independently for each frame; there is no fixed per-frame cap.\nThe original project remains unchanged. Results are saved in this version's output folder.\n")
        self.log.configure(state="disabled")

    def choose(self):
        value=filedialog.askopenfilename(filetypes=[("Video files","*.mp4 *.mov *.mkv *.avi *.webm"),("All files","*.*")])
        if value:
            self.video.set(value)
            cap=cv2.VideoCapture(value)
            self.source_fps=cap.get(cv2.CAP_PROP_FPS) if cap.isOpened() else None
            cap.release()
            if self.source_fps and self.source_fps>0:
                self.status.set(f"Video loaded · source rate {self.source_fps:.3f} FPS. Click Use source FPS to use every source frame.")
            else:
                self.source_fps=None
                self.status.set("Video selected; could not read source FPS.")

    def use_source_fps(self):
        if not self.source_fps:
            messagebox.showinfo("Import a video first","Choose a video so its original FPS can be read.")
            return
        self.fps.set(f"{self.source_fps:.6g}")
        self.status.set(f"Using the clip's source rate: {self.source_fps:.3f} FPS")

    def log_line(self,text):
        self.after(0,self._append,text)

    def _append(self,text):
        self.log.configure(state="normal");self.log.insert("end",text+"\n");self.log.see("end");self.log.configure(state="disabled")

    def start(self):
        if self.running:return
        source=Path(self.video.get()).expanduser()
        if not source.is_file():messagebox.showerror("Video required","Choose a readable video file first.");return
        try:
            seconds=float(self.seconds.get());fps=float(self.fps.get())
            if seconds<=0 or fps<=0:raise ValueError
        except ValueError:messagebox.showerror("Invalid settings","Use positive clip length and FPS values.");return
        self.running=True;self.start_button.configure(state="disabled");self.progress.configure(value=0,maximum=1)
        threading.Thread(target=self._work,args=(source,seconds,fps),daemon=True).start()

    def _work(self,source,seconds,fps):
        cap=cv2.VideoCapture(str(source))
        try:
            if not cap.isOpened():raise RuntimeError("OpenCV could not read this video.")
            src_fps=cap.get(cv2.CAP_PROP_FPS) or 24.0
            total=int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if not total:raise RuntimeError("The video has no readable frames.")
            duration=min(seconds,total/src_fps)
            times=np.arange(0,duration,1.0/fps)
            if not len(times):times=np.array([0.0])
            source_rate=fps>=src_fps*0.99
            if source_rate:cap.set(cv2.CAP_PROP_POS_FRAMES,0)
            job=OUTPUT/datetime.now().strftime("%H-%M-%S_%Y-%m-%d");job.mkdir(parents=True,exist_ok=False)
            frames=[]
            self.log_line(f"Loading AniLines Detail on CPU · {os.cpu_count() or 1} logical CPUs available")
            try:cv2.setNumThreads(1)
            except Exception:pass
            torch.set_num_threads(max(1,min(8,os.cpu_count() or 1)))
            args=SimpleNamespace(mode="detail",device="cpu",fp16=False,binarize=-1.0)
            os.chdir(REPO)
            model=load_model(args)
            self.after(0,lambda m=len(times):self.progress.configure(maximum=m))
            for index,sec in enumerate(times):
                if not source_rate:cap.set(cv2.CAP_PROP_POS_MSEC,float(sec)*1000)
                ok,bgr=cap.read()
                if not ok:continue
                h,w=bgr.shape[:2];scale=min(1.0,1920/max(w,h))
                if scale<1:bgr=cv2.resize(bgr,(round(w*scale),round(h*scale)),interpolation=cv2.INTER_AREA)
                lineart=inference(bgr,model=model,args=args)
                if lineart.ndim==3:gray=cv2.cvtColor(lineart,cv2.COLOR_BGR2GRAY)
                else:gray=lineart
                # VTracer traces the AniLines ink mask into cubic splines.
                ink=np.where(gray<128,255,0).astype(np.uint8)
                ih,iw=ink.shape
                cols=max(4,min(12,round(iw/240)));rows=max(3,min(8,round(ih/216)))
                candidates,path_count=processor._vtracer_curves(ink,None,cols,rows,filter_speckle=1,simplify=.75,min_length=.45)
                frame_budget=len(candidates)
                expressions,regions,cells,face_budget,face_used,blush_limit,body_budget,body_used=processor._emit_vtracer_expressions(candidates,iw,ih,iw,ih,frame_budget,cols,rows)
                frame_number=len(frames)
                image_path=job/f"frame_{frame_number:05d}_anilines.png";cv2.imwrite(str(image_path),lineart)
                curves=[{"id":e["id"],"color":e.get("color","#111111"),"lineWidth":e.get("lineWidth",1),"geometry":e["geometry"],"region":e.get("region","unclassified")} for e in expressions]
                frame_data={"time":round(float(sec),4),"curves":curves,"curve_count":len(curves),"image_width":iw,"image_height":ih,"graph_bounds":{"left":0,"right":iw,"bottom":0,"top":ih},"method":"AniLines Detail raster → VTracer cubic spline curves → JSXGraph","settings":{"vtracer_paths":path_count,"candidate_curves":len(candidates),"dynamic_curve_budget":frame_budget,"emitted_curve_count":len(curves),"curve_budget_policy":"all eligible VTracer curves for this frame; recomputed independently on the next frame","regions":dict(regions)}}
                frames.append(frame_data)
                (job/f"frame_{frame_number:05d}.json").write_text(json.dumps(frame_data,separators=(",",":")),encoding="utf-8")
                self.after(0,lambda v=index+1:self.progress.configure(value=v))
                self.after(0,lambda t=index+1,m=len(times):self.status.set(f"Traced frame {t}/{m} · {len(curves):,} JSXGraph curves"))
                self.log_line(f"Frame {index+1}/{len(times)} · {len(expressions):,}/{frame_budget:,} detected curves · {path_count:,} VTracer paths")
            if not frames:raise RuntimeError("No frames could be processed.")
            payload={"mode":"jsxgraph_anilines_vtracer_animation","sample_fps":fps,"source_fps":src_fps,"used_source_fps":source_rate,"source_video":source.name,"processed_seconds":round(float(times[-1])+1.0/fps,3),"source_width":int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0),"source_height":int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0),"frames":frames}
            json_path=job/"jsxgraph_anilines_vtracer_animation.json";json_path.write_text(json.dumps(payload,separators=(",",":")),encoding="utf-8")
            html_path=job/"jsxgraph_player.html";html_path.write_text(create_jsxgraph_player(),encoding="utf-8")
            mp4_path=MP4_OUTPUT/(datetime.now().strftime("%H-%M-%S_%Y-%m-%d")+".mp4")
            frame_paths=[job/f"frame_{i:05d}.json" for i in range(len(frames))]
            out_size=(int(frames[0]["image_width"]),int(frames[0]["image_height"]))
            self.log_line("Rendering the traced equations to MP4 on CPU…")
            render_animation_to_mp4(frame_paths,mp4_path,fps,out_size,progress=lambda done,total:self.after(0,lambda d=done,t=total:self.status.set(f"CPU video render {d}/{t} frames")))
            self.after(0,lambda:self._done(json_path,html_path,mp4_path,len(frames)))
        except Exception as exc:self.after(0,lambda e=str(exc):self._failed(e))
        finally:
            cap.release();self.after(0,lambda:self.start_button.configure(state="normal"));self.running=False

    def _done(self,json_path,html_path,mp4_path,count):
        self.status.set(f"Finished · {count} traced frames")
        self.player_path=html_path;self.open_player_button.configure(state="normal")
        self.log_line(f"JSXGraph animation JSON: {json_path}");self.log_line(f"JSXGraph player: {html_path}");self.log_line(f"MP4 preview: {mp4_path}")
        webbrowser.open(html_path.as_uri())
        messagebox.showinfo("JSXGraph animation generated",f"{count} frames traced.\n\nJSXGraph animation data:\n{json_path}\n\nJSXGraph player:\n{html_path}\n\nMP4 preview:\n{mp4_path}")

    def open_player(self):
        if self.player_path and self.player_path.is_file():webbrowser.open(self.player_path.as_uri())

    def _failed(self,error):
        self.status.set("Generation failed");self.log_line("ERROR · "+error);messagebox.showerror("Generation failed",error)


if __name__=="__main__":
    Generator().mainloop()
