package compiler

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"gopkg.in/yaml.v3"
)

const sampleProfileYAML = `
apiVersion: kubeagents.x-k8s.io/v1alpha1
kind: KubeAgent
metadata:
  name: test-agent
  namespace: kubeagents-system
spec:
  harness:
    clusterName: "test-cluster"
    location: "us-central1"
    projectId: "test-project"
  personaRef:
    name: standard-operator
    ref: "./personas/standard-operator/SOUL.md"
    content: "Inline SOUL instructions for test-agent."
  skills:
    - name: gke-observability
      ref: "./skills/gke-observability"
      mcpServers:
        - "gcp-monitoring"
  procedures:
    - name: compliance_audit_sop.md
      ref: "./procedures/compliance_audit_sop.md"
  schedules:
    - name: compliance-check
      cron: "0 2 * * *"
      ref: "./schedules/compliance-check.yaml"
  namespaces:
    - "*"
  model:
    provider: "gemini"
    default: "gemini-1.5-pro"
  workflowMode: "Hybrid"
`

func TestParseProfile(t *testing.T) {
	c := NewCompiler()
	agent, err := c.ParseProfile([]byte(sampleProfileYAML))
	if err != nil {
		t.Fatalf("ParseProfile failed: %v", err)
	}

	if agent.Metadata.Name != "test-agent" {
		t.Errorf("expected agent name 'test-agent', got '%s'", agent.Metadata.Name)
	}
	if agent.Spec.WorkflowMode != "Hybrid" {
		t.Errorf("expected workflowMode 'Hybrid', got '%s'", agent.Spec.WorkflowMode)
	}
	if len(agent.Spec.Skills) != 1 || agent.Spec.Skills[0].Name != "gke-observability" {
		t.Errorf("expected skill 'gke-observability', got %+v", agent.Spec.Skills)
	}
}

func TestResolveReference(t *testing.T) {
	c := NewCompiler()
	tmpDir := t.TempDir()

	personaDir := filepath.Join(tmpDir, "personas", "standard-operator")
	if err := os.MkdirAll(personaDir, 0755); err != nil {
		t.Fatal(err)
	}
	personaFile := filepath.Join(personaDir, "SOUL.md")
	if err := os.WriteFile(personaFile, []byte("Resolved Soul Info"), 0644); err != nil {
		t.Fatal(err)
	}

	resolved, content, isRemote, err := c.ResolveReference(tmpDir, "./personas/standard-operator/SOUL.md")
	if err != nil {
		t.Fatalf("ResolveReference failed: %v", err)
	}
	if isRemote {
		t.Errorf("expected local reference, got remote")
	}
	if content != "Resolved Soul Info" {
		t.Errorf("expected content 'Resolved Soul Info', got '%s'", content)
	}
	if resolved != personaFile {
		t.Errorf("expected path '%s', got '%s'", personaFile, resolved)
	}

	remoteURL := "https://example.com/persona.yaml"
	resolvedRemote, _, isRemote, err := c.ResolveReference(tmpDir, remoteURL)
	if err != nil {
		t.Fatalf("ResolveReference remote failed: %v", err)
	}
	if !isRemote {
		t.Errorf("expected remote reference")
	}
	if resolvedRemote != remoteURL {
		t.Errorf("expected remote URL '%s', got '%s'", remoteURL, resolvedRemote)
	}
}

func TestCompileMultiHarness(t *testing.T) {
	tmpDir := t.TempDir()
	profilePath := filepath.Join(tmpDir, "agent-profile.yaml")
	if err := os.WriteFile(profilePath, []byte(sampleProfileYAML), 0644); err != nil {
		t.Fatal(err)
	}

	c := NewCompiler()

	// 1. Test Hermes target
	hermesOut := filepath.Join(tmpDir, "out-hermes")
	if err := c.Compile(profilePath, hermesOut, TargetHermes); err != nil {
		t.Fatalf("Compile hermes failed: %v", err)
	}
	if _, err := os.Stat(filepath.Join(hermesOut, "SOUL.md")); err != nil {
		t.Errorf("expected SOUL.md in Hermes output: %v", err)
	}
	if _, err := os.Stat(filepath.Join(hermesOut, "cron", "jobs.json")); err != nil {
		t.Errorf("expected cron/jobs.json in Hermes output: %v", err)
	}
	if _, err := os.Stat(filepath.Join(hermesOut, "fastmcp", "config.json")); err != nil {
		t.Errorf("expected fastmcp/config.json in Hermes output: %v", err)
	}

	// 2. Test Antigravity target
	antiOut := filepath.Join(tmpDir, "out-anti")
	if err := c.Compile(profilePath, antiOut, TargetAntigravity); err != nil {
		t.Fatalf("Compile antigravity failed: %v", err)
	}
	if _, err := os.Stat(filepath.Join(antiOut, "INDEX.md")); err != nil {
		t.Errorf("expected INDEX.md in Antigravity output: %v", err)
	}
	if _, err := os.Stat(filepath.Join(antiOut, "tools", "groups.json")); err != nil {
		t.Errorf("expected tools/groups.json in Antigravity output: %v", err)
	}

	// 3. Test Scion target
	scionOut := filepath.Join(tmpDir, "out-scion")
	if err := c.Compile(profilePath, scionOut, TargetScion); err != nil {
		t.Fatalf("Compile scion failed: %v", err)
	}
	scionYAML := filepath.Join(scionOut, ".scion", "scion-agent.yaml")
	if _, err := os.Stat(scionYAML); err != nil {
		t.Errorf("expected .scion/scion-agent.yaml in Scion output: %v", err)
	}
	scionData, err := os.ReadFile(scionYAML)
	if err != nil {
		t.Fatalf("failed to read generated scion-agent.yaml: %v", err)
	}
	var scionMap map[string]interface{}
	if err := yaml.Unmarshal(scionData, &scionMap); err != nil {
		t.Errorf("failed to parse generated scion YAML: %v", err)
	}

	// 4. Test Cloud Agents API target
	cloudOut := filepath.Join(tmpDir, "out-cloud")
	if err := c.Compile(profilePath, cloudOut, TargetCloudAgentsAPI); err != nil {
		t.Fatalf("Compile cloud-agents-api failed: %v", err)
	}
	cloudJSON := filepath.Join(cloudOut, "cloud-agent.json")
	if _, err := os.Stat(cloudJSON); err != nil {
		t.Errorf("expected cloud-agent.json in Cloud Agents API output: %v", err)
	}
	cloudData, err := os.ReadFile(cloudJSON)
	if err != nil {
		t.Fatalf("failed to read generated cloud-agent.json: %v", err)
	}
	var cloudPayload map[string]interface{}
	if err := json.Unmarshal(cloudData, &cloudPayload); err != nil {
		t.Errorf("failed to parse generated cloud JSON: %v", err)
	}
	if cloudPayload["displayName"] != "test-agent" {
		t.Errorf("expected displayName 'test-agent', got '%v'", cloudPayload["displayName"])
	}
}
