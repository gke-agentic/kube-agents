package compiler

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	"github.com/gke-agentic/kube-agents/cli/pkg/types"
	"gopkg.in/yaml.v3"
)

// Target constants for multi-harness transpilation.
const (
	TargetHermes         = "hermes"
	TargetAntigravity    = "antigravity"
	TargetScion          = "scion"
	TargetCloudAgentsAPI = "cloud-agents-api"
)

// Compiler handles parsing and transpiling DASP agent profiles.
type Compiler struct {
	HTTPClient *http.Client
}

// NewCompiler creates a new Compiler instance.
func NewCompiler() *Compiler {
	return &Compiler{
		HTTPClient: &http.Client{},
	}
}

// LoadProfile reads and parses a declarative DASP agent-profile.yaml file.
func (c *Compiler) LoadProfile(profilePath string) (*types.KubeAgent, error) {
	data, err := os.ReadFile(profilePath)
	if err != nil {
		return nil, fmt.Errorf("failed to read profile %s: %w", profilePath, err)
	}
	return c.ParseProfile(data)
}

// ParseProfile parses raw YAML bytes into a KubeAgent blueprint.
func (c *Compiler) ParseProfile(data []byte) (*types.KubeAgent, error) {
	var agent types.KubeAgent
	if err := yaml.Unmarshal(data, &agent); err != nil {
		return nil, fmt.Errorf("failed to unmarshal agent profile YAML: %w", err)
	}
	if agent.APIVersion == "" {
		agent.APIVersion = "kubeagents.x-k8s.io/v1alpha1"
	}
	if agent.Kind == "" {
		agent.Kind = "KubeAgent"
	}
	return &agent, nil
}

// ResolveReference resolves a relative or remote reference.
// Returns resolved path/URL and file content if available locally.
func (c *Compiler) ResolveReference(baseDir, ref string) (resolvedPath string, content string, isRemote bool, err error) {
	if ref == "" {
		return "", "", false, nil
	}
	if strings.HasPrefix(ref, "http://") || strings.HasPrefix(ref, "https://") || strings.HasPrefix(ref, "git://") {
		return ref, "", true, nil
	}

	cleanRef := filepath.Clean(ref)
	if filepath.IsAbs(cleanRef) {
		resolvedPath = cleanRef
	} else {
		resolvedPath = filepath.Join(baseDir, cleanRef)
	}

	data, readErr := os.ReadFile(resolvedPath)
	if readErr == nil {
		content = string(data)
	}
	return resolvedPath, content, false, nil
}

// Compile compiles an agent profile YAML file into the specified target harness inside outputDir.
func (c *Compiler) Compile(profilePath, outputDir, target string) error {
	agent, err := c.LoadProfile(profilePath)
	if err != nil {
		return err
	}
	baseDir := filepath.Dir(profilePath)
	return c.CompileAgent(agent, baseDir, outputDir, target)
}

// CompileAgent compiles an in-memory KubeAgent blueprint to the specified target harness.
func (c *Compiler) CompileAgent(agent *types.KubeAgent, baseDir, outputDir, target string) error {
	if err := os.MkdirAll(outputDir, 0755); err != nil {
		return fmt.Errorf("failed to create output dir %s: %w", outputDir, err)
	}

	switch strings.ToLower(target) {
	case TargetHermes:
		return c.compileHermes(agent, baseDir, outputDir)
	case TargetAntigravity:
		return c.compileAntigravity(agent, baseDir, outputDir)
	case TargetScion:
		return c.compileScion(agent, baseDir, outputDir)
	case TargetCloudAgentsAPI:
		return c.compileCloudAgentsAPI(agent, baseDir, outputDir)
	default:
		return fmt.Errorf("unsupported target harness: %s (supported: hermes, antigravity, scion, cloud-agents-api)", target)
	}
}

// compileHermes transpiles to Hermes format (Folder FastMCP + cron/jobs.json + SOUL.md).
func (c *Compiler) compileHermes(agent *types.KubeAgent, baseDir, outputDir string) error {
	// 1. SOUL.md
	soulContent := fmt.Sprintf("# Persona: %s\n\n", agent.Metadata.Name)
	if agent.Spec.PersonaRef != nil {
		resolved, content, isRemote, _ := c.ResolveReference(baseDir, agent.Spec.PersonaRef.Ref)
		soulContent += fmt.Sprintf("Persona Name: %s\nReference: %s (Remote: %v)\n\n", agent.Spec.PersonaRef.Name, resolved, isRemote)
		if content != "" {
			soulContent += content
		} else if agent.Spec.PersonaRef.Content != "" {
			soulContent += agent.Spec.PersonaRef.Content
		}
	}
	if err := os.WriteFile(filepath.Join(outputDir, "SOUL.md"), []byte(soulContent), 0644); err != nil {
		return err
	}

	// 2. cron/jobs.json
	cronDir := filepath.Join(outputDir, "cron")
	if err := os.MkdirAll(cronDir, 0755); err != nil {
		return err
	}
	type cronJob struct {
		Name          string `json:"name"`
		Schedule      string `json:"schedule"`
		TriggerPrompt string `json:"triggerPrompt,omitempty"`
		Ref           string `json:"ref,omitempty"`
	}
	var jobs []cronJob
	for _, s := range agent.Spec.Schedules {
		resolved, _, _, _ := c.ResolveReference(baseDir, s.Ref)
		jobs = append(jobs, cronJob{
			Name:          s.Name,
			Schedule:      s.Cron,
			TriggerPrompt: s.TriggerPrompt,
			Ref:           resolved,
		})
	}
	jobsBytes, err := json.MarshalIndent(jobs, "", "  ")
	if err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(cronDir, "jobs.json"), jobsBytes, 0644); err != nil {
		return err
	}

	// 3. Folder FastMCP structure and config
	fastmcpDir := filepath.Join(outputDir, "fastmcp")
	if err := os.MkdirAll(fastmcpDir, 0755); err != nil {
		return err
	}
	type skillConfig struct {
		Name       string   `json:"name"`
		Ref        string   `json:"ref"`
		MCPServers []string `json:"mcpServers,omitempty"`
	}
	var skillsCfg []skillConfig
	for _, sk := range agent.Spec.Skills {
		resolved, _, _, _ := c.ResolveReference(baseDir, sk.Ref)
		skillsCfg = append(skillsCfg, skillConfig{
			Name:       sk.Name,
			Ref:        resolved,
			MCPServers: sk.MCPServers,
		})

		skillDir := filepath.Join(outputDir, "skills", sk.Name)
		if err := os.MkdirAll(skillDir, 0755); err != nil {
			return err
		}
		skillMD := fmt.Sprintf("---\nname: %s\ndescription: Skill for %s\n---\n# %s\n\nReference path: %s\n", sk.Name, sk.Name, sk.Name, resolved)
		if err := os.WriteFile(filepath.Join(skillDir, "SKILL.md"), []byte(skillMD), 0644); err != nil {
			return err
		}
	}
	cfgBytes, err := json.MarshalIndent(map[string]interface{}{
		"agentName": agent.Metadata.Name,
		"skills":    skillsCfg,
	}, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(fastmcpDir, "config.json"), cfgBytes, 0644)
}

// compileAntigravity transpiles to Antigravity format (Tool groups + Markdown Index).
func (c *Compiler) compileAntigravity(agent *types.KubeAgent, baseDir, outputDir string) error {
	// 1. INDEX.md
	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("# Agent Index: %s\n\n", agent.Metadata.Name))
	sb.WriteString(fmt.Sprintf("## Workflow Mode\nMode: %s\n\n", agent.Spec.WorkflowMode))
	if agent.Spec.PersonaRef != nil {
		resolved, _, _, _ := c.ResolveReference(baseDir, agent.Spec.PersonaRef.Ref)
		sb.WriteString(fmt.Sprintf("## Persona\n- Name: %s\n- Ref: %s\n\n", agent.Spec.PersonaRef.Name, resolved))
	}
	sb.WriteString("## Skills\n")
	for _, sk := range agent.Spec.Skills {
		resolved, _, _, _ := c.ResolveReference(baseDir, sk.Ref)
		sb.WriteString(fmt.Sprintf("- **%s**: `%s`\n", sk.Name, resolved))
	}
	sb.WriteString("\n## Procedures\n")
	for _, pr := range agent.Spec.Procedures {
		resolved, _, _, _ := c.ResolveReference(baseDir, pr.Ref)
		sb.WriteString(fmt.Sprintf("- **%s**: `%s`\n", pr.Name, resolved))
	}
	sb.WriteString("\n## Schedules\n")
	for _, sc := range agent.Spec.Schedules {
		sb.WriteString(fmt.Sprintf("- **%s** (%s)\n", sc.Name, sc.Cron))
	}
	sb.WriteString("\n## Namespaces\n")
	for _, ns := range agent.Spec.Namespaces {
		sb.WriteString(fmt.Sprintf("- `%s`\n", ns))
	}
	if err := os.WriteFile(filepath.Join(outputDir, "INDEX.md"), []byte(sb.String()), 0644); err != nil {
		return err
	}

	// 2. tools/groups.json
	toolsDir := filepath.Join(outputDir, "tools")
	if err := os.MkdirAll(toolsDir, 0755); err != nil {
		return err
	}
	type toolGroup struct {
		GroupName  string   `json:"groupName"`
		Skills     []string `json:"skills"`
		Namespaces []string `json:"namespaces"`
	}
	var groupNames []string
	for _, sk := range agent.Spec.Skills {
		groupNames = append(groupNames, sk.Name)
	}
	tg := toolGroup{
		GroupName:  fmt.Sprintf("%s-tools", agent.Metadata.Name),
		Skills:     groupNames,
		Namespaces: agent.Spec.Namespaces,
	}
	groupsBytes, err := json.MarshalIndent(map[string]interface{}{
		"agent":  agent.Metadata.Name,
		"groups": []toolGroup{tg},
	}, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(toolsDir, "groups.json"), groupsBytes, 0644)
}

// compileScion transpiles to Scion format (.scion/scion-agent.yaml + worktrees).
func (c *Compiler) compileScion(agent *types.KubeAgent, baseDir, outputDir string) error {
	scionDir := filepath.Join(outputDir, ".scion")
	if err := os.MkdirAll(scionDir, 0755); err != nil {
		return err
	}

	type worktreeConfig struct {
		Enabled bool   `yaml:"enabled"`
		Mode    string `yaml:"mode"`
		Path    string `yaml:"path"`
	}
	type scionSpec struct {
		Worktrees    worktreeConfig           `yaml:"worktrees"`
		WorkflowMode string                   `yaml:"workflowMode"`
		Namespaces   []string                 `yaml:"namespaces,omitempty"`
		Persona      *types.PersonaReference  `yaml:"persona,omitempty"`
		Skills       []types.SkillReference   `yaml:"skills,omitempty"`
	}
	type scionAgent struct {
		APIVersion string    `yaml:"apiVersion"`
		Kind       string    `yaml:"kind"`
		Metadata   map[string]string `yaml:"metadata"`
		Spec       scionSpec `yaml:"spec"`
	}

	sa := scionAgent{
		APIVersion: "scion.io/v1",
		Kind:       "ScionAgent",
		Metadata: map[string]string{
			"name": agent.Metadata.Name,
		},
		Spec: scionSpec{
			Worktrees: worktreeConfig{
				Enabled: true,
				Mode:    agent.Spec.WorkflowMode,
				Path:    ".scion/worktrees/" + agent.Metadata.Name,
			},
			WorkflowMode: agent.Spec.WorkflowMode,
			Namespaces:   agent.Spec.Namespaces,
			Persona:      agent.Spec.PersonaRef,
			Skills:       agent.Spec.Skills,
		},
	}

	data, err := yaml.Marshal(&sa)
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(scionDir, "scion-agent.yaml"), data, 0644)
}

// compileCloudAgentsAPI transpiles to Cloud Agents Enterprise JSON API payload.
func (c *Compiler) compileCloudAgentsAPI(agent *types.KubeAgent, baseDir, outputDir string) error {
	type executionConfig struct {
		WorkflowMode string   `json:"workflowMode"`
		Namespaces   []string `json:"namespaces"`
	}
	type cloudAgentPayload struct {
		DisplayName     string                 `json:"displayName"`
		Description     string                 `json:"description"`
		Model           string                 `json:"model"`
		Instructions    string                 `json:"instructions"`
		Skills          []types.SkillReference `json:"skills"`
		ExecutionConfig executionConfig        `json:"executionConfig"`
	}

	modelName := "gemini-1.5-pro"
	if agent.Spec.Model != nil && agent.Spec.Model.Default != "" {
		modelName = agent.Spec.Model.Default
	}

	var instructions string
	if agent.Spec.PersonaRef != nil {
		_, content, _, _ := c.ResolveReference(baseDir, agent.Spec.PersonaRef.Ref)
		if content != "" {
			instructions = content
		} else if agent.Spec.PersonaRef.Content != "" {
			instructions = agent.Spec.PersonaRef.Content
		} else {
			instructions = fmt.Sprintf("Persona: %s (%s)", agent.Spec.PersonaRef.Name, agent.Spec.PersonaRef.Ref)
		}
	}

	payload := cloudAgentPayload{
		DisplayName:  agent.Metadata.Name,
		Description:  fmt.Sprintf("Compiled DASP agent profile for %s", agent.Metadata.Name),
		Model:        modelName,
		Instructions: instructions,
		Skills:       agent.Spec.Skills,
		ExecutionConfig: executionConfig{
			WorkflowMode: agent.Spec.WorkflowMode,
			Namespaces:   agent.Spec.Namespaces,
		},
	}

	payloadBytes, err := json.MarshalIndent(payload, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(outputDir, "cloud-agent.json"), payloadBytes, 0644)
}
