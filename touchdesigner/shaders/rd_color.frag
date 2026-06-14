// Colourise a reaction-diffusion state field into glowing neon.
// GLSL TOP: input 0 = the RD state (R=U, G=V). Output = pretty RGBA.
//
// Uniforms: uTime, uLevel, uHigh, uBeat, uPalette (int), uRes.

out vec4 fragColor;

uniform vec2  uRes;
uniform float uTime;
uniform float uLevel;
uniform float uHigh;
uniform float uBeat;
uniform float uPalette;

void main() {
    vec2 uv = vUV.st;
    vec2 s = texture(sTD2DInputs[0], uv).rg;
    float v = s.y;

    // Edge detection on V emphasises the glowing reaction fronts.
    vec2 texel = 1.0 / uRes;
    float vx = texture(sTD2DInputs[0], uv + vec2(texel.x, 0.0)).g
             - texture(sTD2DInputs[0], uv - vec2(texel.x, 0.0)).g;
    float vy = texture(sTD2DInputs[0], uv + vec2(0.0, texel.y)).g
             - texture(sTD2DInputs[0], uv - vec2(0.0, texel.y)).g;
    float edge = clamp(length(vec2(vx, vy)) * 6.0, 0.0, 1.0);

    float t = fract(v * 2.2 + uTime * 0.03);
    vec3 body = neon(t, int(uPalette)) * smoothstep(0.05, 0.5, v);
    vec3 walls = neon(fract(t + 0.4), int(uPalette)) * edge * (1.4 + uHigh * 2.0);

    vec3 col = body + walls;
    col *= 0.7 + uLevel * 0.8 + uBeat * 0.5;     // pump brightness with the mix
    col = pow(col, vec3(0.85));                  // gentle lift

    fragColor = TDOutputSwizzle(vec4(col, 1.0));
}
