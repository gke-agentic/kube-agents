package types

// Metadata represents Kubernetes metadata for the agent.
type Metadata struct {
	Name      string `yaml:"name" json:"name"`
	Namespace string `yaml:"namespace,omitempty" json:"namespace,omitempty"`
}

// HarnessSpec configures the cluster and environment context.
type HarnessSpec struct {
	ClusterName string `yaml:"clusterName,omitempty" json:"clusterName,omitempty"`
	Location    string `yaml:"location,omitempty" json:"location,omitempty"`
	ProjectID   string `yaml:"projectId,omitempty" json:"projectId,omitempty"`
}

// PersonaReference defines reference or inline content for an agent persona.
type PersonaReference struct {
	Name    string `yaml:"name,omitempty" json:"name,omitempty"`
	Ref     string `yaml:"ref,omitempty" json:"ref,omitempty"`
	Content string `yaml:"content,omitempty" json:"content,omitempty"`
}

// SkillReference defines reference or configuration for an agent skill.
type SkillReference struct {
	Name       string   `yaml:"name,omitempty" json:"name,omitempty"`
	Ref        string   `yaml:"ref,omitempty" json:"ref,omitempty"`
	MCPServers []string `yaml:"mcpServers,omitempty" json:"mcpServers,omitempty"`
	Scripts    []string `yaml:"scripts,omitempty" json:"scripts,omitempty"`
}

// ProcedureReference defines a standard operating procedure or workflow script.
type ProcedureReference struct {
	Name    string `yaml:"name,omitempty" json:"name,omitempty"`
	Ref     string `yaml:"ref,omitempty" json:"ref,omitempty"`
	Content string `yaml:"content,omitempty" json:"content,omitempty"`
}

// ScheduleReference defines a scheduled execution configuration for the agent.
type ScheduleReference struct {
	Name          string `yaml:"name,omitempty" json:"name,omitempty"`
	Cron          string `yaml:"cron,omitempty" json:"cron,omitempty"`
	TriggerPrompt string `yaml:"triggerPrompt,omitempty" json:"triggerPrompt,omitempty"`
	Ref           string `yaml:"ref,omitempty" json:"ref,omitempty"`
}

// ModelSpec configures model provider and defaults for the agent.
type ModelSpec struct {
	Provider string `yaml:"provider,omitempty" json:"provider,omitempty"`
	Default  string `yaml:"default,omitempty" json:"default,omitempty"`
}

// KubeAgentSpec defines the desired state of KubeAgent.
type KubeAgentSpec struct {
	Harness      *HarnessSpec         `yaml:"harness,omitempty" json:"harness,omitempty"`
	PersonaRef   *PersonaReference    `yaml:"personaRef,omitempty" json:"personaRef,omitempty"`
	Skills       []SkillReference     `yaml:"skills,omitempty" json:"skills,omitempty"`
	Procedures   []ProcedureReference `yaml:"procedures,omitempty" json:"procedures,omitempty"`
	Schedules    []ScheduleReference  `yaml:"schedules,omitempty" json:"schedules,omitempty"`
	Namespaces   []string             `yaml:"namespaces,omitempty" json:"namespaces,omitempty"`
	Model        *ModelSpec           `yaml:"model,omitempty" json:"model,omitempty"`
	WorkflowMode string               `yaml:"workflowMode,omitempty" json:"workflowMode,omitempty"`
	ProfileRef   string               `yaml:"profileRef,omitempty" json:"profileRef,omitempty"`
}

// KubeAgent defines the DASP agent profile blueprint.
type KubeAgent struct {
	APIVersion string        `yaml:"apiVersion" json:"apiVersion"`
	Kind       string        `yaml:"kind" json:"kind"`
	Metadata   Metadata      `yaml:"metadata" json:"metadata"`
	Spec       KubeAgentSpec `yaml:"spec" json:"spec"`
}

// Proposal models a pending approval proposal (WAITING_FOR_APPROVAL).
type Proposal struct {
	ID        string `yaml:"id" json:"id"`
	AgentName string `yaml:"agentName" json:"agentName"`
	Status    string `yaml:"status" json:"status"`
	Diff      string `yaml:"diff" json:"diff"`
	Summary   string `yaml:"summary" json:"summary"`
}
