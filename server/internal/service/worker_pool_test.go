package service

import (
	"context"
	"errors"
	"testing"

	db "github.com/multica-ai/multica/server/pkg/db/generated"
	"github.com/multica-ai/multica/server/pkg/dbid"
)

func TestPoolPreparationRequiresPrivateProjectAccess(t *testing.T) {
	ctx := context.Background()
	for _, allowed := range []bool{false, true} {
		name := "denied"
		if allowed {
			name = "allowed"
		}
		t.Run(name, func(t *testing.T) {
			f := newPrivateRunFixture(t, allowed)
			issue, err := f.q.GetIssue(ctx, f.issue)
			if err != nil {
				t.Fatal(err)
			}
			agent, err := f.q.GetAgent(ctx, parseTestUUID(t, f.agent))
			if err != nil {
				t.Fatal(err)
			}
			comment := db.Comment{ID: dbid.NewV7(), AuthorType: "member", AuthorID: parseTestUUID(t, f.UserID), Content: "Plan implementation"}
			_, err = f.svc.TaskSvc.PreparePoolTask(ctx, issue, agent, comment)
			if allowed && err != nil {
				t.Fatal(err)
			}
			if !allowed && !errors.Is(err, ErrProjectAccessDenied) {
				t.Fatalf("expected project access denial, got %v", err)
			}
		})
	}
}
