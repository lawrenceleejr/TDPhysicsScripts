// Master post-FX chain -- the "glue" that makes any scene feel pro and alive.
// GLSL TOP: input 0 = the composited scene. Adds beat-driven zoom punch,
// chromatic aberration, a subtle kaleidoscope fold, scanline shimmer and a
// vignette. Everything is audio/tempo reactive and tasteful at defaults.
//
// Uniforms: uRes, uTime, uLevel, uBeat, uHigh, uBar,
//           uKaleido(float 0..1 mix), uRGBShift(float px), uPunch(float).

out vec4 fragColor;

uniform vec2  uRes;
uniform float uTime;
uniform float uLevel;
uniform float uBeat;
uniform float uHigh;
uniform float uBar;
uniform float uKaleido;
uniform float uRGBShift;
uniform float uPunch;

vec3 sampleScene(vec2 uv) {
    return texture(sTD2DInputs[0], clamp(uv, 0.0, 1.0)).rgb;
}

void main() {
    vec2 uv = vUV.st;
    vec2 c = uv - 0.5;

    // Beat punch: a quick zoom-in that relaxes between beats.
    float zoom = 1.0 - uBeat * 0.06 * uPunch;
    c *= zoom;

    // Optional kaleidoscope fold (mirror into a wedge).
    if (uKaleido > 0.001) {
        float ang = atan(c.y, c.x);
        float rad = length(c);
        float seg = TAU / 6.0;
        ang = abs(mod(ang, seg) - seg * 0.5);
        ang += uTime * 0.05 * uKaleido;
        vec2 k = vec2(cos(ang), sin(ang)) * rad;
        c = mix(c, k, uKaleido);
    }
    uv = c + 0.5;

    // Chromatic aberration scaled by radius + highs.
    vec2 dir = c * (uRGBShift + uHigh * 2.0) / uRes;
    vec3 col;
    col.r = sampleScene(uv + dir).r;
    col.g = sampleScene(uv).g;
    col.b = sampleScene(uv - dir).b;

    // Scanline / shimmer that drifts with the bar.
    float scan = 0.96 + 0.04 * sin(uv.y * uRes.y * 1.2 + uTime * 8.0 + uBar * TAU);
    col *= scan;

    // Vignette + a touch of overall lift on loud passages.
    float vig = smoothstep(1.1, 0.3, length(c));
    col *= vig * (1.0 + uLevel * 0.25);
    col += uBeat * 0.04;

    fragColor = TDOutputSwizzle(vec4(col, 1.0));
}
