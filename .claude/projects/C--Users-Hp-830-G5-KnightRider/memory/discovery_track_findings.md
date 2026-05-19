---
name: discovery_track_findings
description: GP feature evolution v1-v4 results — H3 validated for hand-crafted features, breaks for GP; novel features discovered
type: project
---

Discovery Track ran v1 through v4 (2026-05-16). H3 (composite predicts fault-detection) holds for hand-crafted features (Excel: rho=+0.578 high_damp) but NOT for GP-evolved features. Composite works as a filter (top features are good) but not as a ranker (ordering doesn't match AUC).

**Why:** GP feature space includes "accidentally tight" expressions (higher-order derivatives, rstd wrappers) that fool the composite. Hand-crafted features are diverse by construction so this doesn't happen.

**How to apply:** Don't chase Spearman on GP features. Use composite as a threshold filter. The GP's real value is discovering novel features like rstd(theta)*omega (AUC=0.909) and ddt(omega^2) (AUC=0.780, perfect for damping faults).
