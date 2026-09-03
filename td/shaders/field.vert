// Feynman field — vertex stage.
//
// Passes on the two things the pixel stage needs and TouchDesigner does not
// carry for it: where along its own line this vertex sits, and the line's
// growth and tone, looked up out of the state CHOP.
//
// The four attributes come off the Script SOP by name. sState is a CHOP to TOP
// of the state CHOP: one texel per row, growth in red and tone in green.

uniform sampler2D sState;
uniform float uStateRows;

in float au;      // arc length 0..1 along the line, or a mark quad's -1..1
in float av;      // which side of the centreline, or the quad's other axis
in float eid;     // which row of the state to read
in float kind;    // 0 fermion, 1 boson, 2 scalar, 3 node, 4 vacuum end

out Vertex {
  vec2 uv;
  float grow;
  float tone;
  float kind;
} vs;

void main() {
  vec2 st = texture(sState, vec2((eid + 0.5) / max(1.0, uStateRows), 0.5)).rg;
  vs.grow = st.r;
  vs.tone = st.g;
  vs.uv = vec2(au, av);
  vs.kind = kind;
  gl_Position = TDWorldToProj(TDDeform(P));
}
