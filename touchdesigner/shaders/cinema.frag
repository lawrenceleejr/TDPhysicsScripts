// Cinema pass: the depth-aware finishing that makes a rasterised render read
// as ray traced. GLSL TOP: input 0 = the Render TOP, input 1 = its Depth TOP.
//
//   * screen-space ambient occlusion -- contact darkening where bodies meet
//     and crowd, the single biggest "this was path traced" cue
//   * depth of field -- a thin-lens blur growing with distance from the focus
//     plane (the camera's own Focus / DoF dials on the scene)
//   * atmosphere -- distant bodies sink into a cold haze, so depth reads even
//     through the trails and bloom that follow
//
// Uniforms:  uCam  = (near, far, focus distance, dof strength 0..1)
//            uCine = (ao strength, haze density, time, depth is linear 0/1)
//            uFog  = (haze r, g, b, exposure)

out vec4 fragColor;

#define uRes (uTDOutputInfo.res.zw)  // TD built-in output res (no uniform binding needed)
uniform vec4 uCam;
uniform vec4 uCine;
uniform vec4 uFog;

#define uNear   (uCam.x)
#define uFar    (uCam.y)
#define uFocus  (uCam.z)
#define uDof    (uCam.w)
#define uAO     (uCine.x)
#define uHaze   (uCine.y)
#define uTime   (uCine.z)
#define uLinear (uCine.w)

#define AO_TAPS  12
#define DOF_TAPS 16

// Camera-space distance at uv. A Depth TOP in camera space is already linear;
// a normalized (0..1, hyperbolic) one is linearised with the camera planes.
float depthAt(vec2 uv) {
    float z = texture(sTD2DInputs[1], clamp(uv, 0.0, 1.0)).r;
    if (uLinear > 0.5) return z;
    float zn = z * 2.0 - 1.0;
    return (2.0 * uNear * uFar) / (uFar + uNear - zn * (uFar - uNear));
}

bool isBackground(float d) { return d > uFar * 0.97; }

// Vogel disk: well-spread taps, rotated per pixel to trade banding for noise.
vec2 vogel(int i, int n, float phi) {
    float r = sqrt((float(i) + 0.5) / float(n));
    float th = float(i) * 2.39996323 + phi;
    return r * vec2(cos(th), sin(th));
}

float ambientOcclusion(vec2 uv, float d, float phi) {
    if (isBackground(d)) return 1.0;
    // Sampling radius in pixels shrinks with distance (a fixed world radius).
    float radius = clamp(uRes.y * 0.09 * (uFocus / max(d, 0.1)) * 0.25, 3.0, 48.0);
    float occ = 0.0;
    for (int i = 0; i < AO_TAPS; i++) {
        vec2 o = vogel(i, AO_TAPS, phi) * radius / uRes;
        float ds = depthAt(uv + o);
        float diff = d - ds;                          // > 0: the sample is nearer (occluder)
        float range = 0.12 * d;                       // how far in front still counts
        float w = clamp(diff / range, 0.0, 1.0) * (1.0 - smoothstep(range, range * 4.0, diff));
        occ += w;
    }
    return 1.0 - uAO * (occ / float(AO_TAPS)) * 0.9;
}

void main() {
    vec2 uv = vUV.st;
    float d = depthAt(uv);
    float phi = hash21(gl_FragCoord.xy + fract(uTime) * 7.0) * TAU;

    // Depth of field: circle of confusion from the distance to the focus plane.
    float coc = isBackground(d) ? 0.0
              : clamp(abs(d - uFocus) / max(uFocus, 0.1) * 1.6, 0.0, 1.0) * uDof;
    float radius = coc * uRes.y * 0.012;             // up to ~9 px at 720p
    vec3 col = texture(sTD2DInputs[0], uv).rgb;
    if (radius > 0.5) {
        vec3 acc = col;
        float wsum = 1.0;
        for (int i = 0; i < DOF_TAPS; i++) {
            vec2 o = vogel(i, DOF_TAPS, phi) * radius / uRes;
            vec3 s = texture(sTD2DInputs[0], clamp(uv + o, 0.0, 1.0)).rgb;
            // bright samples spread more (bokeh), like a real lens
            float w = 1.0 + dot(s, vec3(0.6));
            acc += s * w;
            wsum += w;
        }
        col = acc / wsum;
    }

    // Contact shadows.
    col *= ambientOcclusion(uv, d, phi);

    // Atmosphere: haze grows with distance past the focus plane, never on the
    // empty background (the black stays black for the bloom that follows).
    if (!isBackground(d)) {
        float haze = 1.0 - exp(-uHaze * max(d - uFocus * 0.5, 0.0) / max(uFocus, 0.1));
        col = mix(col, uFog.rgb, haze * 0.85);
    }

    fragColor = TDOutputSwizzle(vec4(col * uFog.w, 1.0));
}
