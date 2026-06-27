// File: web/src/components/global/ShaderBloom.tsx
//
// The living bloom — a WebGL rendition of the signature ciel-bleu atmosphere
// (see DESIGN.md "Shaders"). Reworked toward a CLEAN, Capy-style spray bloom:
// a fine-grained particle mist of ciel concentrated in the TOP region of the
// surface that fades smoothly into the background below. Subtle, airy, low
// saturation. NOT a heavy blob, NOT a neon glow, NOT a gradient banner.
//
// Discipline (this is atmosphere, not a tech demo):
//  - One color, sourced from the theme's `--primary` (ciel bleu) — never a
//    hardcoded blue. Light/dark just works because we read the resolved var.
//  - Anchored to the TOP, dissolves to transparent before content density.
//  - Calm: drift is glacial; no flashing, no hue rotation.
//  - Cheap: half-res behind a blur, DPR capped, RAF pauses off-screen/hidden.
//  - prefers-reduced-motion: one static frame (no animation loop).
//  - No WebGL → falls back to a static CSS spray gradient, identical layout.
"use client";

import { useEffect, useRef, useState } from "react";

const RENDER_SCALE = 0.9; // near full-res — the grain must stay crisp, not mush

// Resolve the theme's ciel (`--primary`, authored in oklch) to linear-ish RGB
// in [0,1] by letting the browser do the oklch→sRGB conversion for us, then
// reading back the computed `rgb()`. Falls back to sky-500 if anything fails.
function resolveCiel(): [number, number, number] {
  const fallback: [number, number, number] = [0.149, 0.651, 0.969]; // sky-500
  if (typeof window === "undefined") return fallback;
  try {
    const probe = document.createElement("span");
    probe.style.color = "var(--primary)";
    probe.style.display = "none";
    document.body.appendChild(probe);
    const computed = getComputedStyle(probe).color; // "rgb(r, g, b)" / "oklch(...)"
    document.body.removeChild(probe);
    const m = computed.match(/rgba?\(([^)]+)\)/);
    if (!m) return fallback;
    const parts = m[1].split(/[\s,/]+/).map(Number);
    if (parts.length < 3 || parts.some((n) => Number.isNaN(n))) return fallback;
    return [parts[0] / 255, parts[1] / 255, parts[2] / 255];
  } catch {
    return fallback;
  }
}

const VERT = `
attribute vec2 a_pos;
void main() {
  gl_Position = vec4(a_pos, 0.0, 1.0);
}
`;

// A big, GRAINY, flowing top bloom (the Capy spray). A domain-warped fbm gives
// the moving cloud body; a high-frequency per-pixel hash stipples it into a
// visible spray of fine particles (this is the defining texture — so the canvas
// is NOT heavily blurred, or the grain dies). Denser at the top + corners,
// dissolving down the band before content.
const FRAG = `
precision highp float;
uniform vec2 u_res;
uniform float u_time;
uniform vec3 u_ciel;

float hash(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

float vnoise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  float a = hash(i);
  float b = hash(i + vec2(1.0, 0.0));
  float c = hash(i + vec2(0.0, 1.0));
  float d = hash(i + vec2(1.0, 1.0));
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}

// 5-octave fbm — the real organic body.
float fbm(vec2 p) {
  float v = 0.0;
  float amp = 0.5;
  for (int i = 0; i < 5; i++) {
    v += amp * vnoise(p);
    p = p * 2.03 + vec2(19.1, 7.3);
    amp *= 0.5;
  }
  return v;
}

void main() {
  float aspect = u_res.x / u_res.y;
  vec2 uv = gl_FragCoord.xy / u_res;        // 0,0 bottom-left
  float top = 1.0 - uv.y;                    // 0 at band top, 1 at band bottom
  vec2 p = vec2(uv.x * aspect, uv.y);

  float t = u_time * 0.05;                    // calm, but clearly moving

  // Flowing cloud via domain warp (fbm of fbm) — this is what makes it move and
  // feel like a living spray rather than a static gradient.
  vec2 w = vec2(
    fbm(p * 1.6 + vec2(0.0, t * 1.2)),
    fbm(p * 1.6 + vec2(t * 0.9, 5.2))
  );
  float cloud = fbm(p * 2.3 + w * 2.0 - vec2(0.0, t * 0.6));
  cloud = smoothstep(0.16, 1.0, cloud);      // lower floor = more cloud survives

  // Big top band, densest at the very top and biased HARD into BOTH top corners
  // (the Capy shape: two blooms left + right, dipping through the middle),
  // fading out by ~85% down the band.
  float topMask = smoothstep(0.85, 0.0, top);
  // Each term peaks at its own edge and meets near the centre, so left and
  // right are equally lit and the centre is the quieter trough.
  float corners = smoothstep(0.52, 0.0, uv.x) + smoothstep(0.48, 1.0, uv.x);
  float edge = clamp(0.62 + corners * 1.0, 0.0, 2.0);  // higher floor = present across the top too
  float field = cloud * topMask * edge;

  // GRAIN — per-pixel stipple riding the field. Where the field is dense, more
  // grains survive a lower threshold, so it reads as a spray that thickens
  // toward the top. Slow integer-cell drift gives it life.
  vec2 gp = gl_FragCoord.xy;
  float gr = hash(floor(gp / 1.5) + floor(vec2(t * 3.0, -t * 2.0)));
  float surv = smoothstep(1.0 - field * 0.95, 1.0, gr);

  float base = field * field * 0.62;         // soft cloud body (denser)
  float a = base + surv * field * 1.3;        // + the crisp spray (boosted)
  a *= 1.18;                                  // more intense + present
  a = clamp(a, 0.0, 1.0);

  // One ciel color; grains lift slightly toward white so they read as light.
  vec3 col = mix(u_ciel, vec3(1.0), surv * 0.30);

  gl_FragColor = vec4(col * a, a);           // premultiplied
}
`;

export function ShaderBloom({ className = "" }: { className?: string }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [failed, setFailed] = useState(false);
  // Resolved ciel for the static fallback gradient (so it matches the shader).
  const [ciel, setCiel] = useState<[number, number, number] | null>(null);

  useEffect(() => {
    const rgb = resolveCiel();
    setCiel(rgb);

    const host = hostRef.current;
    const canvas = canvasRef.current;
    if (!host || !canvas) return;

    const gl =
      canvas.getContext("webgl", {
        alpha: true,
        antialias: false,
        depth: false,
        stencil: false,
        premultipliedAlpha: true,
        powerPreference: "low-power",
      }) || canvas.getContext("experimental-webgl");
    if (!gl || !(gl instanceof WebGLRenderingContext)) {
      setFailed(true);
      return;
    }

    const compile = (type: number, src: string) => {
      const shader = gl.createShader(type)!;
      gl.shaderSource(shader, src);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
        console.warn("ShaderBloom:", gl.getShaderInfoLog(shader));
        return null;
      }
      return shader;
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

    // Fullscreen triangle
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

    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.clearColor(0, 0, 0, 0);

    const uRes = gl.getUniformLocation(prog, "u_res");
    const uTime = gl.getUniformLocation(prog, "u_time");
    const uCiel = gl.getUniformLocation(prog, "u_ciel");
    gl.uniform3f(uCiel, rgb[0], rgb[1], rgb[2]);

    const reducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;

    let raf = 0;
    let running = false;
    let visible = true;
    const start = performance.now();

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

    const draw = (timeSec: number) => {
      resize();
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.uniform2f(uRes, canvas.width, canvas.height);
      gl.uniform1f(uTime, timeSec);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    };

    const loop = () => {
      if (!running) return;
      draw((performance.now() - start) / 1000 + 20);
      raf = requestAnimationFrame(loop);
    };

    const setRunning = (next: boolean) => {
      if (reducedMotion) return; // static frame only
      if (next && !running) {
        running = true;
        raf = requestAnimationFrame(loop);
      } else if (!next && running) {
        running = false;
        cancelAnimationFrame(raf);
      }
    };

    // One immediate frame (also the only frame under reduced motion).
    draw(20);

    const io = new IntersectionObserver(
      ([entry]) => {
        visible = entry.isIntersecting;
        setRunning(visible && !document.hidden);
      },
      { threshold: 0 },
    );
    io.observe(host);

    const onVisibility = () => setRunning(visible && !document.hidden);
    document.addEventListener("visibilitychange", onVisibility);

    const ro = new ResizeObserver(() => {
      if (!running) draw(20);
    });
    ro.observe(host);

    return () => {
      running = false;
      cancelAnimationFrame(raf);
      io.disconnect();
      ro.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
      gl.getExtension("WEBGL_lose_context")?.loseContext();
    };
  }, []);

  // Static fallback: a top-anchored ciel spray drawn purely in CSS — same
  // placement + color as the shader, no animation. Used when WebGL is absent.
  if (failed) {
    const c = ciel ?? [0.149, 0.651, 0.969];
    const rgb = `${Math.round(c[0] * 255)}, ${Math.round(c[1] * 255)}, ${Math.round(c[2] * 255)}`;
    return (
      <div
        aria-hidden
        className={`pointer-events-none absolute inset-x-0 top-0 -z-0 h-[860px] max-h-[90vh] overflow-hidden ${className}`}
      >
        <div
          className="absolute inset-x-0 top-0 h-[80%]"
          style={{
            background: `radial-gradient(120% 100% at 50% -15%, rgba(${rgb},0.42) 0%, rgba(${rgb},0.22) 35%, rgba(${rgb},0.07) 60%, transparent 82%)`,
            filter: "blur(22px)",
            maskImage: "linear-gradient(to bottom, black 0%, transparent 100%)",
            WebkitMaskImage:
              "linear-gradient(to bottom, black 0%, transparent 100%)",
          }}
        />
      </div>
    );
  }

  return (
    <div
      ref={hostRef}
      aria-hidden
      className={`pointer-events-none absolute inset-x-0 top-0 -z-0 h-[860px] max-h-[90vh] overflow-hidden ${className}`}
    >
      <canvas
        ref={canvasRef}
        className="h-full w-full"
        style={{ filter: "blur(0.5px) saturate(1.06)" }}
      />
    </div>
  );
}
