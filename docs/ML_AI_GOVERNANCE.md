# ML & AI Governance — VOLTERRA V1

This product uses its predictive model as a governed analytical component, not as an unreviewed answer generator.

## Required lifecycle
1. Validate the model on a held-out or rolling reference split.
2. Register the active/challenger state and evidence source mode.
3. Monitor project-specific drift, calibration/error and decision-impact metrics.
4. Trigger retraining/recalibration using the policy returned by `GET /api/ml/lifecycle`.
5. Allow the domain agent to branch its investigation only when model readiness supports it.
6. Escalate to the original algorithm and OR/simulation layer.
7. Retain final human authority; autonomous operational execution is disabled.

The live agent packet is available from `GET /api/agent/run` and the lifecycle packet from `GET /api/ml/lifecycle`.
