package agent

import (
	"context"
	"encoding/json"
	"fmt"
)

// v3 uses config options even when its initial model catalog is not yet loaded.
func selectKiroSessionModel(ctx context.Context, request acpRequestFn, sessionID, model string, session json.RawMessage, v3 bool) error {
	if _, ok := findACPConfigOption(session, "model"); !ok && !v3 {
		_, err := request(ctx, "session/set_model", map[string]any{"sessionId": sessionID, "modelId": model})
		return err
	}
	result, err := request(ctx, "session/set_config_option", map[string]any{
		"sessionId": sessionID,
		"configId":  "model",
		"value":     model,
	})
	if err != nil {
		return err
	}
	if current, ok := acpConfigOptionCurrentValue(result, "model"); !ok || current != model {
		return fmt.Errorf("kiro did not confirm requested model %q (reported %q)", model, current)
	}
	return nil
}
