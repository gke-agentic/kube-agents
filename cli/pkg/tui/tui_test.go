package tui

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/gke-agentic/kube-agents/cli/pkg/types"
)

func TestRunStudioNonInteractive(t *testing.T) {
	tmpDir := t.TempDir()
	outPath := filepath.Join(tmpDir, "out-agent.yaml")

	err := RunStudio(outPath, true, "test-custodian", "standard-operator", []string{"gke-observability", "cost-optimizer"}, []string{"default", "prod"}, "GitOps")
	if err != nil {
		t.Fatalf("RunStudio non-interactive failed: %v", err)
	}

	data, err := os.ReadFile(outPath)
	if err != nil {
		t.Fatalf("failed reading studio output: %v", err)
	}
	if len(data) == 0 {
		t.Errorf("expected non-empty YAML manifest")
	}
}

func TestRunApproveNonInteractive(t *testing.T) {
	tmpDir := t.TempDir()
	proposalsFile := filepath.Join(tmpDir, "proposals.yaml")

	initialProposals := []types.Proposal{
		{
			ID:        "prop-1",
			AgentName: "test-agent",
			Status:    "WAITING_FOR_APPROVAL",
			Summary:   "Scale up deployment",
			Diff:      "+ replicas: 5",
		},
	}
	if err := SaveProposals(proposalsFile, initialProposals); err != nil {
		t.Fatal(err)
	}

	if err := RunApprove(proposalsFile, true, "prop-1"); err != nil {
		t.Fatalf("RunApprove non-interactive approve failed: %v", err)
	}

	loaded, err := LoadProposals(proposalsFile)
	if err != nil {
		t.Fatal(err)
	}
	if len(loaded) != 1 || loaded[0].Status != "APPROVED" {
		t.Errorf("expected proposal status APPROVED, got %+v", loaded)
	}
}
