package main

import (
	"encoding/json"
	"github.com/spf13/cobra"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestPoolDelegateCLIContract(t *testing.T) {
	redirectTestHome(t, t.TempDir())
	t.Setenv("MULTICA_TOKEN", "test-token")
	t.Setenv("MULTICA_WORKSPACE_ID", "workspace-123")
	const id = "11111111-1111-4111-8111-111111111111"
	for _, tc := range []struct {
		name, body string
		wantError  bool
	}{
		{"valid", `{"pool_id":"` + id + `","agent_id":"` + id + `","task_id":"` + id + `","comment_id":"` + id + `","request_id":"stable","replayed":false}`, false},
		{"missing identifiers", `{"request_id":"stable","replayed":false}`, true},
		{"invalid types", `{"task_id":123}`, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != "POST" || r.URL.Path != "/api/issues/"+id+"/pool-delegations" {
					t.Errorf("unexpected request: %s %s", r.Method, r.URL.Path)
				}
				var body map[string]any
				if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
					t.Error(err)
				}
				if body["pool"] != "kiro" || body["request_id"] != "stable" {
					t.Errorf("incorrect request: %v", body)
				}
				w.Header().Set("Content-Type", "application/json")
				_, _ = w.Write([]byte(tc.body))
			}))
			defer srv.Close()
			t.Setenv("MULTICA_SERVER_URL", srv.URL)
			cmd := &cobra.Command{}
			for k, v := range map[string]string{"server-url": "", "workspace-id": "", "profile": "", "pool": "kiro", "request-id": "stable", "instructions": "work"} {
				cmd.Flags().String(k, v, "")
			}
			cmd.Flags().StringSlice("capability", []string{"code"}, "")
			if err := runPoolDelegate(cmd, []string{id}); (err != nil) != tc.wantError {
				t.Fatalf("error = %v", err)
			}
		})
	}
}
