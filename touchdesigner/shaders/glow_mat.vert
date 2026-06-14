// GLSL MAT vertex stage for rim-lit, audio-reactive instanced points.
// Uses TouchDesigner's instancing/deform helpers so it works on any Geometry
// COMP with Instancing on (instance colour from c3,c4,c5 as set by td_build).

uniform float uLevel;   // smoothed RMS -> swells the points on loud passages
uniform float uBeat;    // beat flash

out Vertex {
    vec3 worldNorm;
    vec3 worldPos;
    vec4 color;
} oVert;

void main() {
    // A gentle scale pump on the beat (kept subtle so it reads as "alive").
    vec3 p = P * (1.0 + uBeat * 0.25 + uLevel * 0.15);
    vec4 worldSpacePos = TDDeform(p);
    gl_Position = TDWorldToProj(worldSpacePos);

    oVert.worldPos = worldSpacePos.xyz;
    oVert.worldNorm = normalize(TDDeformNorm(N));
    oVert.color = TDInstanceColor(vec4(1.0));
}
