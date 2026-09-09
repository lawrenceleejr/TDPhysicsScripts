// The film stock: the pass that decides what the whole show is made of.
//
// Everything above this is physics and effects; this is the grade, the grain
// and the dirt, and it runs over the finished mix so every scene shares one
// look instead of eleven. Aimed at a dark room: crushed, contrasty, split
// toned cold in the shadows and warm in the highlights, haloed around the hot
// bits the way a real lens flares, printed on grain, and never quite steady.
//
// GLSL TOP: input 0 = the composited, effected mix.
//
// Uniforms:
//   uGrain = (grain, halation, dust, weave)
//   uGrade = (contrast, split tone, chaos, time)
//   uAudio = (level, beat, high, pulse)

out vec4 fragColor;

#define uRes (uTDOutputInfo.res.zw)  // TD built-in output res (no uniform binding needed)
uniform vec4 uGrain;
uniform vec4 uGrade;
uniform vec4 uAudio;

#define GRAIN    (uGrain.x)
#define HALATION (uGrain.y)
#define DUST     (uGrain.z)
#define WEAVE    (uGrain.w)
#define CONTRAST (uGrade.x)
#define SPLIT    (uGrade.y)
#define CHAOS    (uGrade.z)
#define TIME     (uGrade.w)
#define LEVEL    (uAudio.x)
#define BEAT     (uAudio.y)
#define HIGH     (uAudio.z)
#define PULSE    (uAudio.w)

// Shadows go cold, highlights go warm: the split that reads as film rather
// than as a colour filter, because it pulls the two ends apart.
const vec3 SHADOW_TONE = vec3(0.62, 0.86, 1.16);
const vec3 HIGH_TONE = vec3(1.14, 1.00, 0.84);

float luma(vec3 c) { return dot(c, vec3(0.2126, 0.7152, 0.0722)); }

vec3 tap(vec2 uv) { return texture(sTD2DInputs[0], clamp(uv, 0.0, 1.0)).rgb; }

// A hash that changes every frame, for grain and dirt that never sit still.
float rnd(vec2 p, float t) { return hash21(p * 1.7 + vec2(t * 91.7, t * 47.3)); }

void main() {
    vec2 uv = vUV.st;
    vec2 c = uv - 0.5;
    float aspect = uRes.x / uRes.y;
    // The whole pass leans on the beat, but through the smooth pulse, so it
    // breathes rather than flickers.
    float energy = clamp(LEVEL * 0.6 + BEAT * 0.4, 0.0, 1.0);
    float chaos = clamp(CHAOS, 0.0, 1.0);

    // ---- gate weave: the frame is never perfectly registered -------------
    float frame = floor(TIME * 24.0);            // hold on a 24 fps gate
    vec2 weave = (vec2(hash21(vec2(frame, 3.0)), hash21(vec2(frame, 11.0))) - 0.5)
                 * WEAVE * 0.004;
    float tilt = (hash21(vec2(frame, 23.0)) - 0.5) * WEAVE * 0.0015;
    c += weave;
    c = mat2(cos(tilt), -sin(tilt), sin(tilt), cos(tilt)) * c;

    // ---- chaos: the frame comes apart, hardest on the beat ---------------
    // Blocks of the image slip sideways, the channels tear, and now and then
    // a band rolls through. All of it scales from one dial so it can be ridden
    // like a filter knob instead of being a switch.
    float bite = chaos * (0.35 + 0.65 * BEAT);
    if (bite > 0.001) {
        float rows = mix(60.0, 14.0, chaos);
        float band = floor((c.y + 0.5) * rows);
        float pick = hash21(vec2(band, frame));
        if (pick < 0.10 + 0.35 * bite) {
            float slip = (hash21(vec2(band, frame + 7.0)) - 0.5) * 0.22 * bite;
            c.x += slip;
        }
        // A wide tear that walks down the frame on the loud passages.
        float roll = fract(TIME * 0.13 + hash21(vec2(floor(TIME * 0.7), 5.0)));
        float d = abs((c.y + 0.5) - roll);
        if (d < 0.02 * bite) c.x += (0.02 + 0.08 * bite) * sign(hash21(vec2(frame, 31.0)) - 0.5);
    }
    uv = c + 0.5;

    // ---- the lens: chromatic aberration that grows toward the corners ----
    float r2 = dot(c * vec2(aspect, 1.0), c * vec2(aspect, 1.0));
    vec2 ca = c * (0.0016 + 0.010 * r2) * (1.0 + 2.2 * bite + 0.6 * HIGH);
    vec3 col;
    col.r = tap(uv + ca).r;
    col.g = tap(uv).g;
    col.b = tap(uv - ca).b;

    // ---- halation: hot areas bleed, warm, the way film does --------------
    if (HALATION > 0.001) {
        vec3 bleed = vec3(0.0);
        float wsum = 0.0;
        for (int i = 0; i < 12; i++) {
            float a = float(i) * TAU / 12.0;
            float rad = (6.0 + 16.0 * float(i % 3)) * (1.0 + 0.5 * energy);
            vec3 s = tap(uv + vec2(cos(a), sin(a) * aspect) * rad / uRes);
            float hot = max(luma(s) - 0.55, 0.0);
            bleed += s * hot;
            wsum += 1.0;
        }
        bleed /= max(wsum, 1.0);
        // Red bleeds furthest: that is the colour of a halation ring.
        col += bleed * vec3(1.35, 0.70, 0.42) * HALATION * 2.6;
    }

    // ---- the grade -------------------------------------------------------
    float l = luma(col);
    // Contrast about a low pivot, so the blacks close up and the highlights
    // keep their room -- the shape of a print, not a curves slider.
    col = mix(vec3(l), col, 1.0 + 0.25 * CONTRAST);           // saturation with it
    col = (col - 0.18) * (1.0 + CONTRAST) + 0.18;
    col = max(col, 0.0);
    // Split tone, weighted by where each pixel sits on the curve.
    float lift = 1.0 - smoothstep(0.0, 0.45, l);
    float gain = smoothstep(0.35, 1.0, l);
    col *= mix(vec3(1.0), SHADOW_TONE, lift * SPLIT);
    col *= mix(vec3(1.0), HIGH_TONE, gain * SPLIT);

    // ---- the print: grain, then dirt ------------------------------------
    if (GRAIN > 0.001) {
        // Grain lives in the mids: film has none in the blacks and little in
        // the blown highlights, and matching that is what keeps it from
        // looking like added noise.
        float mids = 1.0 - abs(luma(col) * 2.0 - 1.0);
        float g = rnd(gl_FragCoord.xy, frame) - 0.5;
        float g2 = rnd(gl_FragCoord.xy * 0.5 + 13.0, frame) - 0.5;
        col += (g * 0.7 + g2 * 0.3) * GRAIN * (0.05 + 0.16 * mids);
    }
    if (DUST > 0.001) {
        // Specks and hairs, resampled a few times a second so they sit on the
        // print rather than crawling.
        float dframe = floor(TIME * 8.0);
        vec2 cell = floor(uv * vec2(220.0, 124.0));
        float speck = hash21(cell + dframe * 37.0);
        if (speck > 1.0 - 0.0022 * DUST) col += vec3(0.55);
        if (speck < 0.0010 * DUST) col *= 0.15;
        // A vertical scratch that persists for a beat or two.
        float sx = hash21(vec2(floor(TIME * 1.7), 3.0));
        float sw = 1.2 / uRes.x;
        if (abs(uv.x - sx) < sw && hash21(vec2(floor(TIME * 1.7), 9.0)) > 0.55) {
            col += vec3(0.20, 0.19, 0.17) * DUST;
        }
    }

    // A last vignette, tight, so the frame closes in a dark room.
    float vig = smoothstep(1.15, 0.35, length(c * vec2(aspect, 1.0)) * 1.25);
    col *= mix(1.0, vig, 0.85);

    col = dither(max(col, 0.0), gl_FragCoord.xy + TIME);
    fragColor = TDOutputSwizzle(vec4(col, 1.0));
}
