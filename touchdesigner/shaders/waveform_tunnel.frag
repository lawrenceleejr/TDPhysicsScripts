// Creative waveform / spectrum visualiser as a transparent overlay.
// GLSL TOP: input 0 = a 1-row TOP holding the audio data across its width --
//   R channel = waveform (-1..1, bias 0.5), G channel = log spectrum (0..1).
//   (td_build builds this from the Reactor with a CHOP-to-TOP.)
//
// Draws a centre scope line + a radial spectrum "iris" that pulses to the mix.
// Output is premultiplied-ish RGBA meant to composite OVER the scene.
//
// Uniforms: uRes, uTime, uLevel, uBeat, uBass, uPalette(int).

out vec4 fragColor;

uniform vec2  uRes;
uniform float uTime;
uniform float uLevel;
uniform float uBeat;
uniform float uBass;
uniform float uPalette;

// input 0 = waveform row (R), input 1 = spectrum row (R) -- separate TOPs so
// their widths can differ (audio buffer vs FFT bins).
float waveform(float x) { return texture(sTD2DInputs[0], vec2(x, 0.5)).r * 2.0 - 1.0; }
float spectrum(float x) { return texture(sTD2DInputs[1], vec2(x, 0.5)).r; }

void main() {
    vec2 uv = vUV.st;
    vec2 p = (gl_FragCoord.xy - 0.5 * uRes) / uRes.y;
    vec3 col = vec3(0.0);
    float alpha = 0.0;

    // --- Horizontal oscilloscope across the lower third ---
    float w = waveform(uv.x) * (0.18 + uLevel * 0.25);
    float scope = smoothstep(0.012, 0.0, abs((uv.y - 0.5) - w));
    col += neon(uv.x, int(uPalette)) * scope * 1.6;
    alpha = max(alpha, scope);

    // --- Radial spectrum iris ---
    float ang = atan(p.y, p.x);
    float rad = length(p);
    float fx = fract(ang / TAU + 0.5);
    float s = spectrum(fx);
    float ring = 0.32 + uBass * 0.12 + uBeat * 0.05;
    float bar = smoothstep(0.02, 0.0, abs(rad - (ring + s * 0.28)));
    vec3 rc = neon(fract(fx + uTime * 0.05), int(uPalette));
    col += rc * bar * (1.2 + uBeat);
    alpha = max(alpha, bar);

    // Soft inner glow ring on the beat.
    float halo = smoothstep(0.02, 0.0, abs(rad - ring)) * (0.3 + uBeat);
    col += rc * halo;
    alpha = max(alpha, halo * 0.6);

    fragColor = TDOutputSwizzle(vec4(col, alpha));
}
