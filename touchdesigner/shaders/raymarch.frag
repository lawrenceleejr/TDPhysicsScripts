// Audio-reactive raymarched SDF -- a morphing, twisting "quantum foam" of
// metaballs that warps to the beat and orbits in time. GLSL TOP, no inputs.
// Lit with diffuse + Fresnel rim + soft shadows + ambient occlusion so the
// blobs read as sculpted form, not flat discs. HDR output -> tonemapped in post.
//
// Uniforms: uTime, uBass, uMid, uHigh, uLevel, uBeat, uBar, uPalette.

out vec4 fragColor;

#define uRes (uTDOutputInfo.res.zw)  // TD built-in output res (no uniform binding needed)
uniform float uTime;
uniform float uBass;
uniform float uMid;
uniform float uHigh;
uniform float uLevel;
uniform float uBeat;
uniform float uBar;
uniform float uPalette;

// Blob centres/radii depend only on time, not on the sample point, so we
// compute them ONCE per pixel (setupBlobs) instead of recomputing 5x sin/cos
// inside every map() call (~120 map() calls/pixel). Big inner-loop win.
vec3  gC[5];
float gR[5];
float gK;   // smooth-union radius

void setupBlobs() {
    float spread = 1.2 + uBar * 0.6;
    for (int i = 0; i < 5; i++) {
        float fi = float(i);
        gC[i] = vec3(sin(uTime * 0.5 + fi * 1.7),
                     cos(uTime * 0.43 + fi * 2.3),
                     sin(uTime * 0.37 + fi * 0.9)) * spread;
        gR[i] = 0.45 + 0.25 * sin(uTime + fi) + uBeat * 0.2;
    }
    gK = 0.6 + uMid * 0.5;
}

float smin(float a, float b, float k) {
    float h = clamp(0.5 + 0.5 * (b - a) / k, 0.0, 1.0);
    return mix(b, a, h) - k * h * (1.0 - h);
}

float map(vec3 p) {
    // Domain warp + twist that ramps with the bass.
    float tw = 0.6 + uBass * 1.8;
    p.xy = rot(p.z * 0.25 * tw + uTime * 0.2) * p.xy;
    p += 0.15 * sin(p.yzx * 3.0 + uTime);

    float d = 1e9;
    for (int i = 0; i < 5; i++) {
        d = smin(d, length(p - gC[i]) - gR[i], gK);
    }
    return d;
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
    setupBlobs();

    // Slowly orbiting camera. Choose the up-reference away from the view dir so
    // cross() never collapses to zero (which would NaN the whole frame).
    float a = uTime * 0.15 + uBar * TAU * 0.1;
    vec3 ro = vec3(sin(a) * 4.0, 0.6 + uMid, cos(a) * 4.0);
    vec3 fwd = safeNorm(-ro);
    vec3 upRef = abs(fwd.y) > 0.99 ? vec3(0.0, 0.0, 1.0) : vec3(0.0, 1.0, 0.0);
    vec3 rgt = safeNorm(cross(upRef, fwd));
    vec3 up  = cross(fwd, rgt);
    vec3 rd  = normalize(uv.x * rgt + uv.y * up + 1.4 * fwd);

    vec3 lightDir = normalize(vec3(0.6, 0.8, 0.4));
    float t = 0.0, glow = 0.0;
    bool hit = false;
    for (int i = 0; i < 96; i++) {
        vec3 p = ro + rd * t;
        float d = map(p);
        glow += 0.02 / (0.01 + abs(d));   // accumulate volumetric glow
        if (d < 0.001) { hit = true; break; }
        if (t > 12.0) break;
        t += d * 0.8;                     // smooth field -> 0.8 understep is safe
    }

    vec3 col = vec3(0.0);
    if (hit) {
        vec3 p = ro + rd * t;
        vec3 n = normal(p);
        float fres = pow(1.0 - max(dot(n, -rd), 0.0), 2.5);
        float diff = clamp(dot(n, lightDir), 0.0, 1.0);
        float sh = softShadow(p + n * 0.02, lightDir, 8.0);
        float occ = ao(p, n);
        float tt = fract(0.5 + 0.3 * p.y + uTime * 0.05 + uHigh);
        vec3 base = neon(tt, int(uPalette));
        col  = base * (0.12 * occ + diff * sh);          // ambient + shadowed key
        col += neon(fract(tt + 0.5), int(uPalette)) * fres * 1.5 * occ;  // rim
    }
    col += neon(fract(uTime * 0.04), int(uPalette)) * glow * 0.05 * (0.5 + uLevel);
    col *= 1.0 + uBeat * 0.6;

    fragColor = TDOutputSwizzle(vec4(col, 1.0));
}
