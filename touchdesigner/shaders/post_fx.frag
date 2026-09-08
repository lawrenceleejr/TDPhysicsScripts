// Master post-FX chain -- the "glue" that makes any scene feel pro and alive,
// plus sixteen whole-screen effects the performer toggles from the APC.
// GLSL TOP: input 0 = the composited scene.
//
// Uniforms (packed vec4s, set by td_build):
//   uAudio = (level, beat, high, bar)
//   uLook  = (kaleido 0..1, rgbshift px, punch, time)
//   uFxA   = (invert, edges, posterize, pixelate)     each 0 or 1
//   uFxB   = (mirror, mono, solarize, fisheye)
//   uFxC   = (tiles, shake, ascii, blur)
//   uFxD   = (kaleidofx, rgbboost, strobe, negflash)
//   uTone  = (exposure, black crush, vignette strength, dark mode 0/1)

out vec4 fragColor;

#define uRes (uTDOutputInfo.res.zw)  // TD built-in output res (no uniform binding needed)
uniform vec4 uAudio;
uniform vec4 uLook;
uniform vec4 uFxA;
uniform vec4 uFxB;
uniform vec4 uFxC;
uniform vec4 uFxD;
uniform vec4 uTone;

#define uLevel    (uAudio.x)
#define uBeat     (uAudio.y)
#define uHigh     (uAudio.z)
#define uBar      (uAudio.w)
#define uKaleido  (uLook.x)
#define uRGBShift (uLook.y)
#define uPunch    (uLook.z)
#define uTime     (uLook.w)

vec3 sampleScene(vec2 uv) {
    return texture(sTD2DInputs[0], clamp(uv, 0.0, 1.0)).rgb;
}

float luma(vec3 c) { return dot(c, vec3(0.299, 0.587, 0.114)); }

// A 5x5 glyph as 25 bits (row-major, top row first). Eight densities:
// ' ' . : - = + * # -> '@' full.
float glyph(int level, vec2 cell) {
    ivec2 p = ivec2(clamp(cell, 0.0, 0.999) * 5.0);
    int bit = (4 - p.y) * 5 + p.x;
    int g;
    if      (level <= 0) g = 0;
    else if (level == 1) g = 0x0001000;   // .   centre dot
    else if (level == 2) g = 0x0020080;   // :   two dots
    else if (level == 3) g = 0x0007C00;   // -   middle row
    else if (level == 4) g = 0x00F83E0;   // =   two rows
    else if (level == 5) g = 0x0427C84;   // +   cross
    else if (level == 6) g = 0x1555555;   // #   checker
    else                 g = 0x1FFFFFF;   // @   full block
    return float((g >> bit) & 1);
}

void main() {
    vec2 uv = vUV.st;
    vec2 c = uv - 0.5;
    float aspect = uRes.x / uRes.y;

    // ---- uv-domain effects ------------------------------------------------
    // Beat punch: a quick zoom-in that relaxes between beats (gentle).
    float zoom = 1.0 - uBeat * 0.035 * uPunch;
    c *= zoom;

    // Shake: a per-frame jolt scaled by the beat.
    if (uFxC.y > 0.5) {
        float j = 0.012 * (0.3 + uBeat);
        c += (vec2(hash21(vec2(floor(uTime * 60.0), 1.0)),
                   hash21(vec2(floor(uTime * 60.0), 7.0))) - 0.5) * j;
    }
    // Fisheye: barrel distortion.
    if (uFxB.w > 0.5) {
        vec2 q = c * vec2(aspect, 1.0);
        float r = length(q);
        q *= 1.0 + 0.9 * r * r;
        c = q / vec2(aspect, 1.0) * 0.72;
    }
    // Kaleidoscope: the Look dial, or the FX toggle at full strength.
    float kal = max(uKaleido, uFxD.x);
    if (kal > 0.001) {
        float ang = atan(c.y, c.x);
        float rad = length(c);
        float seg = TAU / 6.0;
        ang = abs(mod(ang, seg) - seg * 0.5);
        ang += uTime * 0.05 * kal;
        vec2 k = vec2(cos(ang), sin(ang)) * rad;
        c = mix(c, k, kal);
    }
    // Mirror: left half reflected into the right.
    if (uFxB.x > 0.5) c.x = -abs(c.x);
    // Tiles: 3x3 repeat.
    if (uFxC.x > 0.5) c = fract((c + 0.5) * 3.0) - 0.5;
    uv = c + 0.5;
    // Pixelate: quantise to a coarse grid (the ASCII cell grid, if on).
    vec2 cellN = vec2(96.0, 96.0 / aspect);
    if (uFxA.w > 0.5 || uFxC.z > 0.5) {
        uv = (floor(uv * cellN) + 0.5) / cellN;
    }

    // ---- sample, with chromatic aberration ----------------------------------
    float shift = uRGBShift + uHigh * 2.0 + (uFxD.y > 0.5 ? 10.0 + 14.0 * uBeat : 0.0);
    vec2 dir = c * shift / uRes;
    vec3 col;
    col.r = sampleScene(uv + dir).r;
    col.g = sampleScene(uv).g;
    col.b = sampleScene(uv - dir).b;

    // Blur: cheap 9-tap radial blur.
    if (uFxC.w > 0.5) {
        vec3 acc = col;
        for (int i = 1; i <= 8; i++) {
            float a = float(i) * TAU / 8.0;
            acc += sampleScene(uv + vec2(cos(a), sin(a)) * 3.5 / uRes);
        }
        col = acc / 9.0;
    }
    // Edges: Sobel on luminance, drawn in the scene's own colour.
    if (uFxA.y > 0.5) {
        vec2 px = 1.0 / uRes;
        float tl = luma(sampleScene(uv + px * vec2(-1.0,  1.0)));
        float tc = luma(sampleScene(uv + px * vec2( 0.0,  1.0)));
        float tr = luma(sampleScene(uv + px * vec2( 1.0,  1.0)));
        float ml = luma(sampleScene(uv + px * vec2(-1.0,  0.0)));
        float mr = luma(sampleScene(uv + px * vec2( 1.0,  0.0)));
        float bl = luma(sampleScene(uv + px * vec2(-1.0, -1.0)));
        float bc = luma(sampleScene(uv + px * vec2( 0.0, -1.0)));
        float br = luma(sampleScene(uv + px * vec2( 1.0, -1.0)));
        float gx = -tl - 2.0 * ml - bl + tr + 2.0 * mr + br;
        float gy = -tl - 2.0 * tc - tr + bl + 2.0 * bc + br;
        float e = clamp(length(vec2(gx, gy)) * 2.5, 0.0, 1.0);
        vec3 tint = normalize(col + 0.05) * 1.2;
        col = tint * e;
    }
    // ASCII: each cell becomes a glyph whose density follows its brightness.
    if (uFxC.z > 0.5) {
        vec2 cellUV = fract((c + 0.5) * cellN);
        int level = int(clamp(luma(col) * 1.6, 0.0, 1.0) * 7.99);
        float g = glyph(level, cellUV);
        col = normalize(col + 0.02) * (0.4 + 1.2 * luma(col)) * g;
    }

    // ---- colour effects --------------------------------------------------
    if (uFxB.y > 0.5) {                                 // mono, cool tint
        float l = luma(col);
        col = vec3(l) * vec3(0.85, 0.95, 1.1);
    }
    if (uFxA.z > 0.5) col = floor(col * 4.0 + 0.5) / 4.0;              // posterize
    if (uFxB.z > 0.5) col = mix(col, 1.0 - col, step(0.5, col));        // solarize
    if (uFxA.x > 0.5) col = 1.0 - col;                                   // invert
    if (uFxD.w > 0.5 && uBeat > 0.6) col = 1.0 - col;                    // negative on beat
    if (uFxD.z > 0.5) col *= step(0.5, fract(uTime * 9.0));              // strobe

    // Scanline / shimmer that drifts with the bar.
    float scan = 0.96 + 0.04 * sin(uv.y * uRes.y * 1.2 + uTime * 8.0 + uBar * TAU);
    col *= scan;

    // Vignette + a touch of overall lift on loud passages. Dark mode pulls
    // the vignette in tighter.
    bool dark = uTone.w > 0.5;
    float vig = dark ? smoothstep(0.95, 0.22, length(c)) : smoothstep(1.1, 0.3, length(c));
    col *= mix(1.0, vig, uTone.z) * (1.0 + uLevel * 0.15);

    // Expose (a little more on beats) then ACES filmic tonemap.
    col = acesFilm(col * uTone.x * (1.1 + uBeat * 0.18));
    if (dark) {
        // Dark mode: crush the blacks so the ground is truly black and give the
        // mids a slightly heavier gamma -- a screen that reads as dark-mode UI.
        col = max(col - uTone.y, 0.0) / (1.0 - uTone.y);
        col = pow(col, vec3(1.12));
    }
    col = dither(col, gl_FragCoord.xy + uTime);

    fragColor = TDOutputSwizzle(vec4(col, 1.0));
}
