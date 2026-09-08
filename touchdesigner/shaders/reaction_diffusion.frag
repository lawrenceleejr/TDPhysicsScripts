// Gray-Scott reaction-diffusion -- the chemistry of spots, stripes and mitosis.
// GLSL TOP, run in a feedback loop: input 0 is the PREVIOUS state (R=U, G=V),
// ideally a 32-bit float RG TOP. Audio nudges the feed/kill rates so the
// pattern blooms and dissolves with the music. Pair with rd_color.frag.
//
// Uniforms (set by td_build):
//   uRes   vec2   render resolution (px)
//   uTime  float  absTime.seconds
//   uBass uMid uHigh uLevel  float  smoothed audio bands
//   uBeat  float  1.0 on a beat (decays)
//   uFeed uKill  float  base Gray-Scott rates
//   uReseed float pulse: >0.5 reseeds noise + a central blob

out vec4 fragColor;

#define uRes (uTDOutputInfo.res.zw)  // TD built-in output res (no uniform binding needed)
uniform float uTime;
uniform float uBass;
uniform float uMid;
uniform float uHigh;
uniform float uLevel;
uniform float uBeat;
uniform float uFeed;
uniform float uKill;
uniform float uReseed;

vec2 stateAt(vec2 uv) {
    return texture(sTD2DInputs[0], uv).rg;
}

void main() {
    vec2 uv = vUV.st;
    vec2 texel = 1.0 / uRes;

    // Reseed: scatter V with noise + a hot central disc.
    if (uReseed > 0.5) {
        float n = hash21(uv * uRes + uTime);
        vec2 c = uv - 0.5;
        float blob = smoothstep(0.06, 0.0, length(c));
        float v = max(step(0.985, n), blob);
        fragColor = TDOutputSwizzle(vec4(1.0, v, 0.0, 1.0));
        return;
    }

    vec2 s = stateAt(uv);
    // A feedback loop that once holds a NaN holds it forever and the whole
    // frame goes white through the blur; uninitialised float textures can
    // start that way. Any non-finite state resets to the resting chemistry.
    if (any(isnan(s)) || any(isinf(s))) s = vec2(1.0, 0.0);
    s = clamp(s, 0.0, 1.0);
    // 9-point Laplacian (weighted) for U and V.
    vec2 lap = vec2(0.0);
    lap += stateAt(uv + texel * vec2(-1.0,  0.0)) * 0.2;
    lap += stateAt(uv + texel * vec2( 1.0,  0.0)) * 0.2;
    lap += stateAt(uv + texel * vec2( 0.0, -1.0)) * 0.2;
    lap += stateAt(uv + texel * vec2( 0.0,  1.0)) * 0.2;
    lap += stateAt(uv + texel * vec2(-1.0, -1.0)) * 0.05;
    lap += stateAt(uv + texel * vec2( 1.0, -1.0)) * 0.05;
    lap += stateAt(uv + texel * vec2(-1.0,  1.0)) * 0.05;
    lap += stateAt(uv + texel * vec2( 1.0,  1.0)) * 0.05;
    lap -= s;
    if (any(isnan(lap)) || any(isinf(lap))) lap = vec2(0.0);

    float u = s.x, v = s.y;
    // Sparse sparks, a few pixels a frame, more on a beat: the pattern can
    // never die out into the dead (u=1, v=0) state, whatever the seed did,
    // and a drop re-ignites it.
    float spark = step(0.99985 - 0.0008 * uBeat,
                       hash21(uv * uRes * 0.37 + fract(uTime * 7.31) * 113.0));
    v = max(v, spark * 0.85);
    float Du = 0.16, Dv = 0.08;

    // Audio drives the feed/kill window -- this is what makes it "breathe".
    float feed = uFeed + uMid * 0.012 + uBeat * 0.006;
    float kill = uKill + uHigh * 0.006;

    float reaction = u * v * v;
    float du = Du * lap.x - reaction + feed * (1.0 - u);
    float dv = Dv * lap.y + reaction - (kill + feed) * v;

    // Two sub-steps per frame keep it stable at 60 fps.
    float dt = 1.0;
    u = clamp(u + du * dt, 0.0, 1.0);
    v = clamp(v + dv * dt, 0.0, 1.0);

    fragColor = TDOutputSwizzle(vec4(u, v, 0.0, 1.0));
}
