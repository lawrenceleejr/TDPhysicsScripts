// Audio-reactive raymarched SDF -- a morphing, twisting "quantum foam" of
// metaballs that warps to the beat and orbits in time. GLSL TOP, no inputs.
//
// Uniforms: uRes, uTime, uBass, uMid, uHigh, uLevel, uBeat, uBar, uPalette(int).

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

float sdSphere(vec3 p, float r) { return length(p) - r; }

// Smooth-union of blobs => organic metaball field.
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
    float k = 0.6 + uMid * 0.5;
    for (int i = 0; i < 5; i++) {
        float fi = float(i);
        vec3 c = vec3(
            sin(uTime * 0.5 + fi * 1.7),
            cos(uTime * 0.43 + fi * 2.3),
            sin(uTime * 0.37 + fi * 0.9)
        ) * (1.2 + uBar * 0.6);
        float r = 0.45 + 0.25 * sin(uTime + fi) + uBeat * 0.2;
        d = smin(d, sdSphere(p - c, r), k);
    }
    return d;
}

vec3 normal(vec3 p) {
    vec2 e = vec2(0.001, 0.0);
    return normalize(vec3(
        map(p + e.xyy) - map(p - e.xyy),
        map(p + e.yxy) - map(p - e.yxy),
        map(p + e.yyx) - map(p - e.yyx)
    ));
}

void main() {
    vec2 uv = (gl_FragCoord.xy - 0.5 * uRes) / uRes.y;

    // Slowly orbiting camera, nudged by the bar phase.
    float a = uTime * 0.15 + uBar * TAU * 0.1;
    vec3 ro = vec3(sin(a) * 4.0, 0.6 + uMid, cos(a) * 4.0);
    vec3 fwd = normalize(-ro);
    vec3 rgt = normalize(cross(vec3(0.0, 1.0, 0.0), fwd));
    vec3 up  = cross(fwd, rgt);
    vec3 rd  = normalize(uv.x * rgt + uv.y * up + 1.4 * fwd);

    float t = 0.0;
    float glow = 0.0;
    bool hit = false;
    for (int i = 0; i < 90; i++) {
        vec3 p = ro + rd * t;
        float d = map(p);
        glow += 0.02 / (0.01 + abs(d));   // accumulate volumetric glow
        if (d < 0.001) { hit = true; break; }
        if (t > 12.0) break;
        t += d * 0.7;
    }

    vec3 col = vec3(0.0);
    if (hit) {
        vec3 p = ro + rd * t;
        vec3 n = normal(p);
        float fres = pow(1.0 - max(dot(n, -rd), 0.0), 2.5);
        float diff = clamp(dot(n, normalize(vec3(0.6, 0.8, 0.4))), 0.0, 1.0);
        float tt = fract(0.5 + 0.3 * p.y + uTime * 0.05 + uHigh);
        col = neon(tt, int(uPalette)) * (0.3 + diff);
        col += neon(fract(tt + 0.5), int(uPalette)) * fres * 1.5;   // rim light
    }
    col += neon(fract(uTime * 0.04), int(uPalette)) * glow * 0.05 * (0.5 + uLevel);
    col *= 1.0 + uBeat * 0.6;

    fragColor = TDOutputSwizzle(vec4(col, 1.0));
}
