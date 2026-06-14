// Shared GLSL helpers for the physics-VJ shaders.
//
// TouchDesigner GLSL TOP note: this file is *prepended* to each pixel shader by
// td_build (TD has no #include), so keep it self-contained and side-effect free.

#ifndef TDPHYS_COMMON
#define TDPHYS_COMMON

const float PI  = 3.14159265359;
const float TAU = 6.28318530718;

// Inigo Quilez cosine palette -- cheap, smooth, and great for neon-on-black.
vec3 pal(float t, vec3 a, vec3 b, vec3 c, vec3 d) {
    return a + b * cos(TAU * (c * t + d));
}

// A small bank of neon palettes selected by index (matches physics/palette.py
// in spirit, not exact values). 0 inferno,1 magma,2 plasma,3 cyber,4 synth,
// 5 acid,6 ice.
vec3 neon(float t, int which) {
    t = clamp(t, 0.0, 1.0);
    if (which == 0) return pal(t, vec3(0.5), vec3(0.5), vec3(1.0, 0.9, 0.7), vec3(0.0, 0.15, 0.35));
    if (which == 1) return pal(t, vec3(0.45, 0.2, 0.3), vec3(0.55), vec3(1.0, 0.8, 0.6), vec3(0.0, 0.1, 0.2));
    if (which == 2) return pal(t, vec3(0.5), vec3(0.5), vec3(1.0, 1.0, 0.5), vec3(0.8, 0.9, 0.3));
    if (which == 3) return pal(t, vec3(0.2, 0.5, 0.6), vec3(0.4, 0.5, 0.5), vec3(1.0), vec3(0.0, 0.33, 0.66));
    if (which == 4) return pal(t, vec3(0.6, 0.3, 0.6), vec3(0.45), vec3(1.0, 0.7, 1.0), vec3(0.3, 0.2, 0.5));
    if (which == 5) return pal(t, vec3(0.4, 0.6, 0.2), vec3(0.5), vec3(1.0, 1.0, 0.3), vec3(0.1, 0.5, 0.2));
    return pal(t, vec3(0.3, 0.5, 0.7), vec3(0.4, 0.45, 0.5), vec3(1.0), vec3(0.5, 0.6, 0.8)); // ice
}

float hash21(vec2 p) {
    p = fract(p * vec2(123.34, 456.21));
    p += dot(p, p + 45.32);
    return fract(p.x * p.y);
}

mat2 rot(float a) {
    float c = cos(a), s = sin(a);
    return mat2(c, -s, s, c);
}

#endif
