// File: web/src/components/global/GlassShader.tsx
//
// Light glass — a WebGL frosted-glass hero backdrop. Domain-warped flow refracts
// slow ciel-bleu light through a near-white base, with bright specular sweeps
// (the "glass shine"). Tuned to the Lyra LIGHT theme: airy, premium, calm.
//
// This is the ONE atmospheric exception DESIGN.md sanctions (the sky→indigo
// bloom). It is meant to read as ATMOSPHERE, not a feature — render it behind
// content at low opacity, edge-masked.
//
// Discipline (DESIGN.md "Shaders"): near full-res, DPR capped, RAF paused
// off-screen / tab-hidden, prefers-reduced-motion renders one static frame,
// no WebGL → a static CSS gradient fallback. Lazy-load this with `ssr: false`
// (see GlassShaderLazy) so it never blocks first paint or breaks SSR.
"use client";

import { useEffect, useRef, useState } from "react";

// Crisp rendering — the fluted-glass streaks and film grain are the look;
// blurring them away would destroy it. Near-native resolution, no CSS blur.
const RENDER_SCALE = 0.9;

const VERT = `
attribute vec2 a_pos;
void main() { gl_Position = vec4(a_pos, 0.0, 1.0); }
`;

// A luminous aurora band sweeping diagonally, refracted through FLUTED GLASS —
// vertical ribs that smear the light and emit thin bright streak lines at the
// rib centers — film grain, slow undulation, slight chromatic fringe. Lyra
// LIGHT palette: ciel-bleu aurora with a white-hot core on a near-white field.
const FRAG = `
precision highp float;
uniform vec2 u_res;
uniform float u_time;
uniform vec2 u_drift;
uniform float u_dark;   // 0 = Lyra light, 1 = Lyra dark (sky→indigo bloom)

float hash(vec2 p){ p = fract(p*vec2(123.34,456.21)); p += dot(p,p+45.32); return fract(p.x*p.y); }
float vnoise(vec2 p){
  vec2 i=floor(p), f=fract(p);
  float a=hash(i), b=hash(i+vec2(1.,0.)), c=hash(i+vec2(0.,1.)), d=hash(i+vec2(1.,1.));
  vec2 u=f*f*(3.-2.*f);
  return mix(mix(a,b,u.x),mix(c,d,u.x),u.y);
}
float fbm(vec2 p){
  float v=0., amp=0.5;
  for(int i=0;i<4;i++){ v+=amp*vnoise(p); p=p*2.02+vec2(11.3,7.7); amp*=0.5; }
  return v;
}

float aurora(vec2 p, float t, float stretch, out float core){
  float yc = 0.74 - 0.46*p.x
           + 0.06*sin(p.x*1.9 + t*0.58)
           + 0.035*sin(p.x*4.3 - t*0.38);
  float d = (p.y - yc)/stretch;
  float along = fbm(vec2(p.x*1.1 - t*0.19, t*0.09));
  float breathe = 0.72 + 0.28*sin(t*0.32);
  float w = (0.21 - 0.03*p.x) + 0.06*along;
  w = max(w, 0.09);
  float band = exp(-d*d/(2.0*w*w)) * (0.58 + 0.66*along) * breathe;
  vec2 hotC = vec2(0.52 + 0.16*sin(t*0.26), yc - 0.03 + 0.05*sin(t*0.21));
  float hd = distance(vec2(p.x, p.y), hotC);
  float pocket = exp(-hd*hd/(2.0*0.15*0.15)) * (0.78 + 0.34*sin(t*0.23+1.7));
  band += pocket*1.25;
  core = exp(-d*d/(2.0*pow(w*0.34,2.0))) * (0.46 + 0.66*along) * breathe
       + pocket*1.05;
  return band;
}

void main(){
  vec2 uv = gl_FragCoord.xy/u_res;
  float t = u_time;
  float va = 1.6;

  float panels = 28.0;
  float xr = uv.x * panels;
  float cell = floor(xr);
  float fr = fract(xr);
  float cx = (cell + 0.5)/panels;

  float smearX = mix(uv.x, cx, 0.86);
  vec2 ps = vec2((smearX + u_drift.x*0.4)*va, uv.y + u_drift.y*0.4);

  float core;
  float band = aurora(ps, t, 1.15, core);

  // content protection: keep the glass calm where the hero copy sits
  float protectMask = exp(-pow(distance(uv, vec2(0.24, 0.40))*2.3, 2.0));
  band *= 1.0 - 0.52*protectMask;
  core *= 1.0 - 0.58*protectMask;

  float coreR; aurora(ps + vec2(0.004,0.0), t, 1.15, coreR);
  float coreB; aurora(ps - vec2(0.004,0.0), t, 1.15, coreB);
  coreR *= 1.0 - 0.58*protectMask;
  coreB *= 1.0 - 0.58*protectMask;

  float edge = min(fr, 1.0 - fr);
  float seam = exp(-pow(edge*panels*2.6, 2.0));
  float coreE;
  float fieldAtEdge = aurora(vec2((cell/panels + u_drift.x*0.4)*va, uv.y + u_drift.y*0.4), t, 1.5, coreE);
  fieldAtEdge *= 1.0 - 0.55*protectMask;
  float seamGlow = seam * (0.14 + 1.05*fieldAtEdge);

  bool dk = u_dark > 0.5;
  vec3 bgA  = dk ? vec3(0.030, 0.040, 0.066) : vec3(0.980, 0.986, 0.996);
  vec3 bgB  = dk ? vec3(0.046, 0.058, 0.092) : vec3(0.945, 0.962, 0.990);
  vec3 deep = dk ? vec3(0.090, 0.180, 0.520) : vec3(0.075, 0.215, 0.660);
  vec3 ciel = dk ? vec3(0.300, 0.560, 1.000) : vec3(0.165, 0.455, 0.950);
  vec3 lift = dk ? vec3(0.180, 0.380, 0.880) : vec3(0.520, 0.730, 0.985);
  vec3 hot  = dk ? vec3(0.820, 0.920, 1.000) : vec3(0.995, 0.998, 1.0);

  vec3 col = mix(bgB, bgA, smoothstep(0.0, 1.0, uv.x*0.6 + uv.y*0.5));

  float pvar = hash(vec2(cell, 7.0));
  col = mix(col, mix(bgB, lift, 0.22), 0.04 + 0.07*pvar);
  col *= 1.0 - 0.018*cos(fr*6.28318);

  float b = clamp(band, 0.0, 1.6);
  col = mix(col, lift, smoothstep(0.06, 0.50, b));
  col = mix(col, ciel, smoothstep(0.34, 0.92, b));
  col = mix(col, deep, smoothstep(0.78, 1.35, b)*0.85);
  col = mix(col, hot,  clamp((coreR+core+coreB)*0.50, 0.0, 1.0));
  col.r += (coreR - coreB)*0.05;
  col.b += (coreB - coreR)*0.05;

  vec3 seamCol = mix(hot, mix(lift, hot, 0.5), 0.4);
  col = mix(col, seamCol, clamp(seamGlow, 0.0, 0.85));

  float vig = smoothstep(1.25, 0.45, distance(uv, vec2(0.5, 0.55)));
  col = mix(col, bgB, (1.0 - vig)*0.20);

  float g = hash(gl_FragCoord.xy + vec2(fract(t*7.0)*311.0, fract(t*13.0)*97.0));
  col += (g - 0.5) * 0.045;

  gl_FragColor = vec4(clamp(col, 0.0, 1.0), 1.0);
}
`;

export function GlassShader({
  className = "",
  dark = false,
}: {
  className?: string;
  /** Use the dark sky→indigo palette instead of the airy light field. */
  dark?: boolean;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [failed, setFailed] = useState(false);
  // Bumped to re-run GL setup (context restored, bfcache return, cached canvas
  // coming back with a dead context).
  const [gen, setGen] = useState(0);

  useEffect(() => {
    const host = hostRef.current;
    const canvas = canvasRef.current;
    if (!host || !canvas) return;

    const gl =
      (canvas.getContext("webgl", {
        alpha: false,
        antialias: false,
        depth: false,
        stencil: false,
        powerPreference: "low-power",
      }) as WebGLRenderingContext | null) ||
      (canvas.getContext("experimental-webgl") as WebGLRenderingContext | null);
    if (!gl || !(gl instanceof WebGLRenderingContext)) {
      setFailed(true);
      return;
    }

    const onCtxLost = (e: Event) => {
      e.preventDefault();
    };
    const onCtxRestored = () => setGen((g) => g + 1);
    canvas.addEventListener("webglcontextlost", onCtxLost);
    canvas.addEventListener("webglcontextrestored", onCtxRestored);
    if (gl.isContextLost()) {
      gl.getExtension("WEBGL_lose_context")?.restoreContext();
      return () => {
        canvas.removeEventListener("webglcontextlost", onCtxLost);
        canvas.removeEventListener("webglcontextrestored", onCtxRestored);
      };
    }

    const compile = (type: number, src: string) => {
      const s = gl.createShader(type)!;
      gl.shaderSource(s, src);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
        console.warn("GlassShader:", gl.getShaderInfoLog(s));
        return null;
      }
      return s;
    };
    const vs = compile(gl.VERTEX_SHADER, VERT);
    const fs = compile(gl.FRAGMENT_SHADER, FRAG);
    if (!vs || !fs) {
      setFailed(true);
      return;
    }
    const prog = gl.createProgram()!;
    gl.attachShader(prog, vs);
    gl.attachShader(prog, fs);
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      setFailed(true);
      return;
    }
    gl.useProgram(prog);

    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 3, -1, -1, 3]),
      gl.STATIC_DRAW,
    );
    const loc = gl.getAttribLocation(prog, "a_pos");
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

    const uRes = gl.getUniformLocation(prog, "u_res");
    const uTime = gl.getUniformLocation(prog, "u_time");
    const uDrift = gl.getUniformLocation(prog, "u_drift");
    const uDark = gl.getUniformLocation(prog, "u_dark");

    const reduced = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;

    let raf = 0;
    let running = false;
    let visible = true;
    const start = performance.now();
    const drift = { x: 0, y: 0, tx: 0, ty: 0 };

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 1.5) * RENDER_SCALE;
      const w = Math.max(1, Math.round(host.clientWidth * dpr));
      const h = Math.max(1, Math.round(host.clientHeight * dpr));
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
        gl.viewport(0, 0, w, h);
      }
    };
    const draw = (sec: number) => {
      resize();
      drift.x += (drift.tx - drift.x) * 0.03;
      drift.y += (drift.ty - drift.y) * 0.03;
      gl.uniform2f(uRes, canvas.width, canvas.height);
      gl.uniform1f(uTime, sec);
      gl.uniform1f(uDark, dark ? 1 : 0);
      gl.uniform2f(
        uDrift,
        drift.x + Math.sin(sec * 0.23) * 0.05,
        drift.y + Math.cos(sec * 0.17) * 0.035,
      );
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    };
    const loop = () => {
      if (!running) return;
      draw((performance.now() - start) / 1000 + 12);
      raf = requestAnimationFrame(loop);
    };
    const setRunning = (next: boolean) => {
      if (reduced) return;
      if (next && !running) {
        running = true;
        raf = requestAnimationFrame(loop);
      } else if (!next && running) {
        running = false;
        cancelAnimationFrame(raf);
      }
    };

    draw(12); // immediate first frame (also the only one under reduced motion)

    const io = new IntersectionObserver(
      ([e]) => {
        visible = e.isIntersecting;
        setRunning(visible && !document.hidden);
      },
      { threshold: 0 },
    );
    io.observe(host);
    const onVis = () => setRunning(visible && !document.hidden);
    document.addEventListener("visibilitychange", onVis);
    const onPointer = (e: PointerEvent) => {
      drift.tx = (e.clientX / window.innerWidth - 0.5) * 0.11;
      drift.ty = (0.5 - e.clientY / window.innerHeight) * 0.075;
    };
    window.addEventListener("pointermove", onPointer, { passive: true });
    const ro = new ResizeObserver(() => {
      if (!running) draw(12);
    });
    ro.observe(host);

    const onPageShow = (e: PageTransitionEvent) => {
      if (e.persisted) setGen((g) => g + 1);
    };
    window.addEventListener("pageshow", onPageShow);

    return () => {
      running = false;
      cancelAnimationFrame(raf);
      io.disconnect();
      ro.disconnect();
      document.removeEventListener("visibilitychange", onVis);
      window.removeEventListener("pointermove", onPointer);
      window.removeEventListener("pageshow", onPageShow);
      canvas.removeEventListener("webglcontextlost", onCtxLost);
      canvas.removeEventListener("webglcontextrestored", onCtxRestored);
    };
  }, [gen, dark]);

  if (failed) {
    return (
      <div
        aria-hidden
        className={`pointer-events-none absolute inset-0 z-0 ${className}`}
        style={{
          background: dark
            ? "radial-gradient(120% 90% at 50% 0%, #16223f 0%, #0a1020 45%, #060810 100%)"
            : "radial-gradient(120% 90% at 50% 0%, #eaf3ff 0%, #f6faff 45%, #ffffff 100%)",
        }}
      />
    );
  }

  return (
    <div
      ref={hostRef}
      aria-hidden
      className={`pointer-events-none absolute inset-0 z-0 overflow-hidden ${className}`}
    >
      <canvas
        ref={canvasRef}
        className="h-full w-full"
        style={{ filter: "saturate(1.05)" }}
      />
      {/* dissolve the glass into the page before content density begins */}
      <div className="absolute inset-x-0 bottom-0 h-1/2 bg-gradient-to-b from-transparent to-[var(--background)]" />
    </div>
  );
}

export default GlassShader;
