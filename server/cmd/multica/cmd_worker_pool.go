package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"os"

	"github.com/multica-ai/multica/server/internal/cli"
	"github.com/spf13/cobra"
)

func init() {
	delegate := &cobra.Command{Use: "delegate <issue-id>", Short: "Delegate to the next eligible worker in a squad pool", Args: exactArgs(1), RunE: runPoolDelegate}
	delegate.Flags().String("pool", "", "Worker pool name (required)")
	delegate.Flags().String("request-id", "", "Stable ID for this delegation; reuse on retries (required)")
	delegate.Flags().String("instructions", "", "Work to delegate (required)")
	delegate.Flags().StringSlice("capability", nil, "Required worker capabilities (repeatable)")
	delegate.Flags().String("output", "json", "Output format (json)")
	squadCmd.AddCommand(delegate)
	pool := &cobra.Command{Use: "pool", Short: "Configure ordered squad worker pools"}
	list := &cobra.Command{Use: "list <squad-id>", Args: exactArgs(1), RunE: func(cmd *cobra.Command, args []string) error {
		client, err := newAPIClient(cmd)
		if err != nil {
			return err
		}
		ctx, cancel := cli.APIContext(context.Background())
		defer cancel()
		var result []map[string]any
		if err = client.GetJSON(ctx, "/api/squads/"+url.PathEscape(args[0])+"/pools", &result); err != nil {
			return err
		}
		return cli.PrintJSON(os.Stdout, result)
	}}
	set := &cobra.Command{Use: "set <squad-id> <pool-name>", Short: "Replace ordered pool members from a JSON file", Args: exactArgs(2), RunE: func(cmd *cobra.Command, args []string) error {
		file, _ := cmd.Flags().GetString("file")
		if file == "" {
			return fmt.Errorf("--file is required (JSON object with members array)")
		}
		data, err := os.ReadFile(file)
		if err != nil {
			return err
		}
		var body map[string]any
		if err = json.Unmarshal(data, &body); err != nil {
			return fmt.Errorf("invalid pool JSON")
		}
		client, err := newAPIClient(cmd)
		if err != nil {
			return err
		}
		ctx, cancel := cli.APIContext(context.Background())
		defer cancel()
		var result map[string]any
		if err = client.PutJSON(ctx, "/api/squads/"+url.PathEscape(args[0])+"/pools/"+url.PathEscape(args[1]), body, &result); err != nil {
			return err
		}
		return cli.PrintJSON(os.Stdout, result)
	}}
	set.Flags().String("file", "", "Pool configuration JSON file")
	pool.AddCommand(list, set)
	squadCmd.AddCommand(pool)
}

func runPoolDelegate(cmd *cobra.Command, args []string) error {
	body := map[string]any{}
	for _, name := range []string{"pool", "request-id", "instructions"} {
		value, _ := cmd.Flags().GetString(name)
		if value == "" {
			return fmt.Errorf("--%s is required", name)
		}
		key := name
		if key == "request-id" {
			key = "request_id"
		}
		body[key] = value
	}
	caps, _ := cmd.Flags().GetStringSlice("capability")
	if caps == nil {
		caps = []string{}
	}
	body["capabilities"] = caps
	client, err := newAPIClient(cmd)
	if err != nil {
		return err
	}
	ctx, cancel := cli.APIContext(context.Background())
	defer cancel()
	issue, err := resolveIssueRef(ctx, client, args[0])
	if err != nil {
		return err
	}
	var result poolDelegationOutput
	if err = client.PostJSON(ctx, "/api/issues/"+issue.ID+"/pool-delegations", body, &result); err != nil {
		return fmt.Errorf("delegate to pool: %w", err)
	}
	if err = result.validate(body["request_id"].(string)); err != nil {
		return err
	}
	return cli.PrintJSON(os.Stdout, result)
}

type poolDelegationOutput struct {
	PoolID    string `json:"pool_id"`
	RequestID string `json:"request_id"`
	AgentID   string `json:"agent_id"`
	TaskID    string `json:"task_id"`
	CommentID string `json:"comment_id"`
	Replayed  *bool  `json:"replayed"`
}

func (out poolDelegationOutput) validate(requestID string) error {
	if out.RequestID != requestID || out.Replayed == nil {
		return fmt.Errorf("invalid delegation response; retry with the same request-id")
	}
	for _, id := range []string{out.PoolID, out.AgentID, out.TaskID, out.CommentID} {
		if !uuidRegexp.MatchString(id) {
			return fmt.Errorf("invalid delegation response; retry with the same request-id")
		}
	}
	return nil
}
