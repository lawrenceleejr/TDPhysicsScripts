// Procedural HDR studio environment for image-based lighting.
// GLSL TOP, no inputs, rendered small (512x256, 16-bit float) and fed to an
// Environment Light COMP. This is what makes PBR spheres read as ray traced:
// a big warm softbox reflects as a soft window highlight, a cool strip light
// behind gives every body a cold edge, the dark studio walls give the shading
// somewhere to fall off to, and a faint floor bounce keeps undersides alive.
//
// Equirectangular: u = azimuth, v = elevation. Values are HDR (softbox ~ 9).
// Uniforms: uEnv = (time, intensity, 0, 0) -- the lights drift very slowly so
// the reflections are never a still photograph.

out vec4 fragColor;

#define uRes (uTDOutputInfo.res.zw)  // TD built-in output res (no uniform binding needed)
uniform vec4 uEnv;

#define uTime      (uEnv.x)
#define uIntensity (uEnv.y)

// A soft-edged rectangle of light on the sphere, in (azimuth, elevation).
float softbox(vec2 ae, vec2 centre, vec2 half_size, float soft) {
    vec2 d = abs(ae - centre);
    d.x = min(d.x, TAU - d.x);                 // wrap around in azimuth
    vec2 e = smoothstep(half_size + soft, half_size - soft * 0.4, d);
    return e.x * e.y;
}

void main() {
    vec2 uv = vUV.st;
    float az = (uv.x - 0.5) * TAU;            // -pi .. pi
    float el = (uv.y - 0.5) * PI;             // -pi/2 .. pi/2
    vec2 ae = vec2(az, el);
    float drift = sin(uTime * 0.05) * 0.15;

    // Dark studio: near-black walls, a hint of blue above, warm-grey floor.
    vec3 col = mix(vec3(0.020, 0.018, 0.022), vec3(0.015, 0.02, 0.045), smoothstep(-0.2, 1.2, el));
    col = mix(col, vec3(0.05, 0.042, 0.036), smoothstep(-0.05, -0.9, el)) ;   // floor bounce

    // Key softbox: big, warm, high on the front-left.
    float key = softbox(ae, vec2(-0.9 + drift, 0.75), vec2(0.55, 0.28), 0.18);
    col += vec3(1.0, 0.86, 0.68) * 9.0 * key;
    // Its faint spill halo so the reflection has a gradient, not a hard cut.
    col += vec3(1.0, 0.9, 0.75) * 0.35 * softbox(ae, vec2(-0.9 + drift, 0.7), vec2(1.1, 0.6), 0.5);

    // Cool strip light: low and behind, long and thin -- the cold edge.
    float strip = softbox(ae, vec2(2.6 - drift, -0.05), vec2(1.2, 0.05), 0.08);
    col += vec3(0.45, 0.65, 1.0) * 6.0 * strip;

    // A small hard practical on the right for a second, sharper highlight.
    float prac = softbox(ae, vec2(1.3, 0.35), vec2(0.10, 0.10), 0.05);
    col += vec3(1.0, 0.55, 0.35) * 5.0 * prac;

    fragColor = TDOutputSwizzle(vec4(col * uIntensity, 1.0));
}
