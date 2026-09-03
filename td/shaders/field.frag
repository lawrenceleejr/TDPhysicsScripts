// Feynman field — pixel stage.
//
// Does four things the CPU used to do stroke by stroke:
//
//   trims a line to the flood's growth, by discarding anything past it;
//   dashes the scalars off the same arc-length coordinate;
//   cuts a disc or a cross out of a mark's quad;
//   colours by kind and fades by tone.
//
// Alphas and weights are design/network.js's: a fermion at 0.88, a boson at
// 0.72, a scalar at 0.85 in the accent, a node at 0.92 and a vacuum end at
// 0.88. The palette is uniforms so a VJ can push it.

uniform vec3 uInk;
uniform vec3 uAccent;
uniform float uDash;        // dashes per unit length along a scalar
uniform float uSoft;        // edge softness across a line, 0..1
uniform float uBright;      // overall gain

in Vertex {
  vec2 uv;
  float grow;
  float tone;
  float kind;
} vs;

out vec4 fragColor;

void main() {
  float kind = vs.kind;
  float tone = vs.tone * uBright;
  if (tone <= 0.004) discard;

  vec3 col = uInk;
  float alpha = 0.88;

  if (kind < 2.5) {
    // A line. uv.x is arc length, uv.y is across it.
    if (vs.uv.x > vs.grow) discard;                 // not grown this far yet
    if (kind > 1.5) {                               // scalar: dashed
      if (fract(vs.uv.x * uDash) > 0.5) discard;
      col = uAccent;
      alpha = 0.85;
    } else if (kind > 0.5) {                        // boson
      alpha = 0.72;
    }
    // Soften the two long edges of the ribbon so a thin line does not crawl.
    float across = 1.0 - abs(vs.uv.y);
    alpha *= smoothstep(0.0, max(0.001, uSoft), across);
  } else if (kind < 3.5) {
    // A node: a filled disc inside its quad.
    float r = length(vs.uv) * 1.25;                 // build() padded the quad
    alpha = 0.92 * (1.0 - smoothstep(1.0 - uSoft, 1.0, r));
    if (alpha <= 0.004) discard;
  } else {
    // A vacuum end: two strokes across the quad.
    vec2 p = vs.uv * 1.25;
    float d = min(abs(p.x - p.y), abs(p.x + p.y)) * 0.7071;   // to either diagonal
    float arm = max(abs(p.x), abs(p.y));
    if (arm > 1.0) discard;
    float w = 0.18;
    alpha = 0.88 * (1.0 - smoothstep(w - uSoft * w, w, d));
    if (alpha <= 0.004) discard;
  }

  fragColor = TDOutputSwizzle(vec4(col, alpha * tone));
}
