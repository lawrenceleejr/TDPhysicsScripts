// Ink-bleed title overlay. GLSL TOP: input 0 = a Text TOP (the scene's title
// in big caps, white on transparent). Output: RGBA to composite OVER the show.
//
// The letters soak in like ink on wet paper: edges roughen with noise, a halo
// of blurred alpha grows into tendrils as the bleed progresses, and the whole
// thing fades out again on release. Cream ink with a dark bloom underneath so
// it reads on any scene.
//
// Uniforms: uInk = (bleed 0..1 soaking in, fade 0..1 staying visible,
//                   time, unused)

out vec4 fragColor;

#define uRes (uTDOutputInfo.res.zw)  // TD built-in output res (no uniform binding needed)
uniform vec4 uInk;

#define uBleed (uInk.x)
#define uFade  (uInk.y)
#define uTime  (uInk.z)

float vnoise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    float a = hash21(i), b = hash21(i + vec2(1.0, 0.0));
    float c = hash21(i + vec2(0.0, 1.0)), d = hash21(i + vec2(1.0, 1.0));
    return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}

float fbm(vec2 p) {
    float v = 0.0, a = 0.5;
    for (int i = 0; i < 4; i++) {
        v += a * vnoise(p);
        p = p * 2.03 + 11.7;
        a *= 0.5;
    }
    return v;
}

float ink(vec2 uv) { return texture(sTD2DInputs[0], clamp(uv, 0.0, 1.0)).a; }

void main() {
    vec2 uv = vUV.st;
    float aspect = uRes.x / uRes.y;
    float t = uTime * 0.07;
    float bleed = clamp(uBleed, 0.0, 1.0);

    // Roughen the edge: sample the text through a noise displacement that
    // grows with the bleed (dry letters first, then the paper takes them).
    vec2 nuv = uv * vec2(aspect, 1.0) * 28.0;
    vec2 disp = (vec2(fbm(nuv + t), fbm(nuv + 31.0 - t)) - 0.5) * (0.004 + 0.016 * bleed);
    float core = ink(uv + disp);

    // Halo: the alpha blurred over a radius that grows with the bleed.
    float radius = (0.004 + 0.035 * bleed);
    float halo = 0.0;
    for (int i = 0; i < 12; i++) {
        float a = float(i) * TAU / 12.0;
        float r = radius * (0.5 + 0.5 * float(i % 2));
        halo += ink(uv + vec2(cos(a) / aspect, sin(a)) * r);
    }
    halo /= 12.0;

    // Tendrils: the halo thresholded through noise, so the bleed reaches out
    // unevenly rather than as a uniform glow.
    float grain = fbm(uv * vec2(aspect, 1.0) * 90.0 - t * 3.0);
    float soak = smoothstep(0.55 - 0.42 * bleed, 0.75, halo + (grain - 0.5) * 0.35 * bleed);
    float letters = smoothstep(0.35, 0.6, core);
    float inkAll = max(letters, soak * (0.35 + 0.65 * bleed));

    // Paper grain in the ink itself.
    inkAll *= 0.82 + 0.18 * grain;

    vec3 cream = vec3(0.937, 0.914, 0.855);
    vec3 shadow = vec3(0.02, 0.015, 0.02);
    float shade = smoothstep(0.02, 0.5, halo) * 0.85;      // dark bloom under the ink

    float alpha = max(inkAll, shade) * clamp(uFade, 0.0, 1.0);
    vec3 col = mix(shadow, cream, inkAll / max(max(inkAll, shade), 1e-4));
    fragColor = TDOutputSwizzle(vec4(col, alpha));
}
