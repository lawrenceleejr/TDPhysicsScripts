// Colourise a reaction-diffusion state field into glowing neon.
// GLSL TOP: input 0 = the RD state (R=U, G=V). Output = pretty RGBA.
//
// Uniforms: uTime, uLevel, uHigh, uBeat, uPalette (int), uInvert, uRes.

out vec4 fragColor;

#define uRes (uTDOutputInfo.res.zw)  // TD built-in output res (no uniform binding needed)
uniform float uTime;
uniform float uLevel;
uniform float uHigh;
uniform float uBeat;
uniform float uPalette;
uniform float uInvert;      // 1 = light the other side of the field

void main() {
    vec2 uv = vUV.st;
    vec2 s = texture(sTD2DInputs[0], uv).rg;
    // The chemistry's V field: inverted, the background becomes the lit body
    // and the spots become the holes in it.
    float v = (uInvert > 0.5) ? (1.0 - s.y) : s.y;

    // Edge detection on V emphasises the glowing reaction fronts.
    vec2 texel = 1.0 / uRes;
    float vx = texture(sTD2DInputs[0], uv + vec2(texel.x, 0.0)).g
             - texture(sTD2DInputs[0], uv - vec2(texel.x, 0.0)).g;
    float vy = texture(sTD2DInputs[0], uv + vec2(0.0, texel.y)).g
             - texture(sTD2DInputs[0], uv - vec2(0.0, texel.y)).g;
    // The edge is the same wherever the field is steep, inverted or not.
    float edge = clamp(length(vec2(vx, vy)) * 6.0, 0.0, 1.0);

    float t = fract(v * 2.2 + uTime * 0.03);
    vec3 body = neon(t, int(uPalette)) * smoothstep(0.05, 0.5, v);
    vec3 walls = neon(fract(t + 0.4), int(uPalette)) * edge * (1.4 + uHigh * 2.0);

    vec3 col = body + walls;
    col *= 0.7 + uLevel * 0.8 + uBeat * 0.5;     // pump brightness with the mix
    col = pow(max(col, 0.0), vec3(0.85));        // gentle lift (clamp: pow(neg) is UB)

    fragColor = TDOutputSwizzle(vec4(col, 1.0));
}
