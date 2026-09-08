// Audio-reactive raymarched SDF scene -- four forms, all controllable.
// GLSL TOP, no inputs. Lit with diffuse + Fresnel rim + soft shadows + ambient
// occlusion so the forms read as sculpted, not flat. HDR out -> tonemapped in
// the master post chain.
//
// Uniforms (packed vec4s so they fit in a few Vectors slots; set by td_build):
//   uAudio = (bass, mid, high, level)
//   uTempo = (beat, pulse, time, palette)   pulse = the smooth beat pulsation
//   uCtrl  = (shape, speed, twist, zoom)      shape 0..3, see below
//   uCtrl2 = (detail, morph, 0, 0)            detail 1..4, morph 0..1
//
// Shapes:  0 metaballs (3 + detail blobs, smooth-unioned)
//          1 gyroid lattice carved from a sphere
//          2 kaleidoscopic IFS fractal (detail = fold iterations)
//          3 a wobbling torus knot / ring

out vec4 fragColor;

#define uRes (uTDOutputInfo.res.zw)  // TD built-in output res (no uniform binding needed)
uniform vec4 uAudio;
uniform vec4 uTempo;
uniform vec4 uCtrl;
uniform vec4 uCtrl2;

#define uBass    (uAudio.x)
#define uMid     (uAudio.y)
#define uHigh    (uAudio.z)
#define uLevel   (uAudio.w)
#define uBeat    (uTempo.x)
#define uPulse   (uTempo.y)
#define uTime    (uTempo.z)
#define uPalette (uTempo.w)
#define uShape   (uCtrl.x)
#define uSpeed   (uCtrl.y)
#define uTwist   (uCtrl.z)
#define uZoom    (uCtrl.w)
#define uDetail  (uCtrl2.x)
#define uMorph   (uCtrl2.y)

// The form's own clock: strictly the speed dial times real time. Nothing that
// wraps may enter it -- the bar phase used to, and a sawtooth that resets is
// exactly the jerk you saw: the whole form and the camera snapped back once a
// bar. The tempo reaches the geometry only through the smooth pulse.
float T;
int   gShape;
int   gDetail;

// --- metaballs (shape 0): centres depend only on time, computed once per pixel
vec3  gC[8];
float gR[8];
float gK;

void setupBlobs() {
    float spread = (1.1 + uMorph * 0.9) + uPulse * 0.15;
    int n = 3 + gDetail;
    for (int i = 0; i < 8; i++) {
        if (i >= n) break;
        float fi = float(i);
        gC[i] = vec3(sin(T * 0.5 + fi * 1.7),
                     cos(T * 0.43 + fi * 2.3),
                     sin(T * 0.37 + fi * 0.9)) * spread;
        gR[i] = 0.42 + 0.22 * sin(T + fi) + uPulse * 0.08;
    }
    gK = 0.5 + uMid * 0.5 + uMorph * 0.4;
}

float smin(float a, float b, float k) {
    float h = clamp(0.5 + 0.5 * (b - a) / k, 0.0, 1.0);
    return mix(b, a, h) - k * h * (1.0 - h);
}

float sdMetaballs(vec3 p) {
    float d = 1e9;
    int n = 3 + gDetail;
    for (int i = 0; i < 8; i++) {
        if (i >= n) break;
        d = smin(d, length(p - gC[i]) - gR[i], gK);
    }
    return d;
}

// --- gyroid lattice (shape 1), carved from a sphere that swells on the beat
float sdGyroid(vec3 p) {
    float k = 2.2 + uMorph * 2.5 + uBass * 0.6;
    vec3 q = p * k + vec3(0.0, T * 0.6, 0.0);
    float g = abs(dot(sin(q), cos(q.zxy))) / k - (0.06 + 0.05 * uMorph);
    float shell = length(p) - (2.1 + uPulse * 0.12);
    return max(g, shell);
}

// --- kaleidoscopic IFS (shape 2): fold, sort, scale; detail = iterations
float sdFractal(vec3 p) {
    float s = 1.0;
    float scale = 2.6 + 0.6 * uMorph + 0.3 * sin(T * 0.3);
    vec3 off = vec3(1.0, 1.0, 1.0) * (1.6 + 0.4 * uMid);
    int iters = 2 + gDetail;
    for (int i = 0; i < 6; i++) {
        if (i >= iters) break;
        p = abs(p);
        if (p.x < p.y) p.xy = p.yx;
        if (p.x < p.z) p.xz = p.zx;
        if (p.y < p.z) p.yz = p.zy;
        p = p * scale - off * (scale - 1.0);
        p.xz = rot(0.15 + 0.1 * uMorph) * p.xz;
        s *= scale;
    }
    // a soft-cornered box as the base primitive
    vec3 q = abs(p) - vec3(1.0);
    float box = length(max(q, 0.0)) - 0.15;
    return box / s;
}

// --- torus knot / ring (shape 3): a tube whose radius ripples around it
float sdKnot(vec3 p) {
    float R = 1.5 + 0.3 * uMorph;
    float ang = atan(p.z, p.x);
    float lobes = float(3 + gDetail);
    float ripple = 0.14 * sin(ang * lobes + T * 2.0) + 0.05 * uPulse;
    vec2 c = vec2(length(p.xz) - R, p.y - 0.35 * sin(ang * (lobes - 1.0) - T * 1.3));
    return length(c) - (0.32 + ripple);
}

float map(vec3 p) {
    // Domain twist about z, ramping with the bass and the Twist dial.
    float tw = uTwist * (0.4 + uBass * 1.4);
    p.xy = rot(p.z * 0.35 * tw + T * 0.15) * p.xy;
    p += 0.12 * uMorph * sin(p.yzx * 3.0 + T);
    if (gShape == 1) return sdGyroid(p);
    if (gShape == 2) return sdFractal(p);
    if (gShape == 3) return sdKnot(p);
    return sdMetaballs(p);
}

vec3 normal(vec3 p) {
    vec2 e = vec2(0.0015, 0.0);
    vec3 g = vec3(map(p + e.xyy) - map(p - e.xyy),
                  map(p + e.yxy) - map(p - e.yxy),
                  map(p + e.yyx) - map(p - e.yyx));
    return safeNorm(g);
}

// Soft shadow by marching toward the light (penumbra ~ 1/k).
float softShadow(vec3 ro, vec3 rd, float k) {
    float res = 1.0, t = 0.05;
    for (int i = 0; i < 18; i++) {
        float h = map(ro + rd * t);
        if (h < 0.001) return 0.0;
        res = min(res, k * h / t);
        t += clamp(h, 0.03, 0.4);
        if (t > 7.0) break;
    }
    return clamp(res, 0.0, 1.0);
}

// Ambient occlusion from the distance field (5 short taps along the normal).
float ao(vec3 p, vec3 n) {
    float occ = 0.0, sca = 1.0;
    for (int i = 0; i < 5; i++) {
        float hr = 0.01 + 0.13 * float(i) / 4.0;
        occ += (hr - map(p + n * hr)) * sca;
        sca *= 0.85;
    }
    return clamp(1.0 - 1.6 * occ, 0.0, 1.0);
}

void main() {
    vec2 uv = (gl_FragCoord.xy - 0.5 * uRes) / uRes.y;
    T = uTime * uSpeed;
    gShape = int(clamp(uShape + 0.5, 0.0, 3.0));
    gDetail = int(clamp(uDetail + 0.5, 1.0, 4.0));
    setupBlobs();

    // Slowly orbiting camera; Zoom moves it in and out. Up-reference chosen
    // away from the view dir so cross() never collapses (NaN frame).
    float a = T * 0.15;                              // one continuous orbit
    float dist = 4.2 / max(uZoom, 0.2);
    vec3 ro = vec3(sin(a) * dist, 0.6 + uMid * 0.35 + 0.4 * sin(T * 0.11), cos(a) * dist);
    vec3 fwd = safeNorm(-ro);
    vec3 upRef = abs(fwd.y) > 0.99 ? vec3(0.0, 0.0, 1.0) : vec3(0.0, 1.0, 0.0);
    vec3 rgt = safeNorm(cross(upRef, fwd));
    vec3 up  = cross(fwd, rgt);
    vec3 rd  = normalize(uv.x * rgt + uv.y * up + 1.4 * fwd);

    vec3 lightDir = normalize(vec3(0.6, 0.8, 0.4));
    float t = 0.0, glow = 0.0;
    bool hit = false;
    float stepScale = (gShape == 2) ? 0.6 : 0.8;     // fractals want smaller steps
    for (int i = 0; i < 110; i++) {
        vec3 p = ro + rd * t;
        float d = map(p);
        glow += 0.02 / (0.01 + abs(d));   // accumulate volumetric glow
        if (d < 0.001) { hit = true; break; }
        if (t > 14.0) break;
        t += d * stepScale;
    }

    vec3 col = vec3(0.0);
    int pal = int(uPalette + 0.5);
    if (hit) {
        vec3 p = ro + rd * t;
        vec3 n = normal(p);
        float fres = pow(1.0 - max(dot(n, -rd), 0.0), 2.5);
        float diff = clamp(dot(n, lightDir), 0.0, 1.0);
        float sh = softShadow(p + n * 0.02, lightDir, 8.0);
        float occ = ao(p, n);
        float tt = fract(0.5 + 0.3 * p.y + T * 0.05 + uHigh);
        vec3 base = neon(tt, pal);
        col  = base * (0.12 * occ + diff * sh);                 // ambient + shadowed key
        col += neon(fract(tt + 0.5), pal) * fres * 1.5 * occ;   // rim
    }
    col += neon(fract(T * 0.04), pal) * glow * 0.05 * (0.5 + uLevel);
    col *= 1.0 + uBeat * 0.3;

    fragColor = TDOutputSwizzle(vec4(col, 1.0));
}
