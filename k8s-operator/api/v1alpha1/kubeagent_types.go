/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// PersonaReference defines reference or inline content for an agent persona.
type PersonaReference struct {
	// Name is the logical name of the persona.
	// +optional
	Name string `json:"name,omitempty"`

	// Ref references a remote or filesystem blueprint persona.
	// +optional
	Ref string `json:"ref,omitempty"`

	// Content contains inline persona instructions.
	// +optional
	Content string `json:"content,omitempty"`
}

// SkillReference defines reference or configuration for an agent skill.
type SkillReference struct {
	// Name is the name of the skill.
	// +optional
	Name string `json:"name,omitempty"`

	// Ref references a skill blueprint path or URI.
	// +optional
	Ref string `json:"ref,omitempty"`

	// MCPServers lists MCP server configurations or endpoints required by the skill.
	// +optional
	MCPServers []string `json:"mcpServers,omitempty"`

	// Scripts lists script names or paths associated with the skill.
	// +optional
	Scripts []string `json:"scripts,omitempty"`
}

// ProcedureReference defines a standard operating procedure or workflow script.
type ProcedureReference struct {
	// Name is the name of the procedure.
	// +optional
	Name string `json:"name,omitempty"`

	// Ref references a procedure file or path.
	// +optional
	Ref string `json:"ref,omitempty"`

	// Content contains inline procedure definition.
	// +optional
	Content string `json:"content,omitempty"`
}

// ScheduleReference defines a scheduled execution configuration for the agent.
type ScheduleReference struct {
	// Name is the identifier for this schedule.
	// +optional
	Name string `json:"name,omitempty"`

	// Cron specifies the cron expression for execution.
	// +optional
	Cron string `json:"cron,omitempty"`

	// TriggerPrompt specifies the prompt to trigger when the cron fires.
	// +optional
	TriggerPrompt string `json:"triggerPrompt,omitempty"`

	// Ref references a scheduled workflow or procedure.
	// +optional
	Ref string `json:"ref,omitempty"`
}

// ModelSpec configures model provider and defaults for the agent.
type ModelSpec struct {
	// Provider specifies the model provider (e.g. vertex, openai).
	// +optional
	Provider string `json:"provider,omitempty"`

	// Default specifies the default model name.
	// +optional
	Default string `json:"default,omitempty"`
}

// KubeAgentIntegrationSpec extends IntegrationSpec with GoogleChat integration.
type KubeAgentIntegrationSpec struct {
	IntegrationSpec `json:",inline"`

	// GoogleChat configures the Google Chat integration.
	// +optional
	GoogleChat *GoogleChatSpec `json:"googleChat,omitempty"`
}

// KubeAgentSpec defines the desired state of KubeAgent.
type KubeAgentSpec struct {
	AgentSpec `json:",inline"`

	// Harness configures the core execution environment and framework-level settings.
	// +optional
	Harness *HarnessSpec `json:"harness,omitempty"`

	// Integration configures external integrations.
	// +optional
	Integration *KubeAgentIntegrationSpec `json:"integration,omitempty"`

	// PersonaRef defines the persona blueprint or inline content.
	// +optional
	PersonaRef *PersonaReference `json:"personaRef,omitempty"`

	// Skills lists the skills attached to this agent.
	// +optional
	Skills []SkillReference `json:"skills,omitempty"`

	// Procedures lists standard operating procedures attached to this agent.
	// +optional
	Procedures []ProcedureReference `json:"procedures,omitempty"`

	// Schedules lists cron schedules attached to this agent.
	// +optional
	Schedules []ScheduleReference `json:"schedules,omitempty"`

	// Namespaces defines the target namespaces where this agent should have RBAC permissions.
	// Use ["*"] for cluster-wide permissions.
	// +optional
	Namespaces []string `json:"namespaces,omitempty"`

	// Model configures the model settings.
	// +optional
	Model *ModelSpec `json:"model,omitempty"`

	// WorkflowMode configures execution mode (e.g. autonomous, interactive).
	// +optional
	WorkflowMode string `json:"workflowMode,omitempty"`

	// ProfileRef references a profile configuration.
	// +optional
	ProfileRef string `json:"profileRef,omitempty"`
}

// KubeAgentStatus defines the observed state of KubeAgent.
type KubeAgentStatus struct {
	AgentStatus `json:",inline"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status

// KubeAgent is the Schema for the kubeagents API.
type KubeAgent struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	// Spec defines the desired state of KubeAgent.
	// +required
	Spec KubeAgentSpec `json:"spec"`

	// Status defines the observed state of KubeAgent.
	// +optional
	Status KubeAgentStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// KubeAgentList contains a list of KubeAgent.
type KubeAgentList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitzero"`
	Items           []KubeAgent `json:"items"`
}

func init() {
	SchemeBuilder.Register(&KubeAgent{}, &KubeAgentList{})
}
