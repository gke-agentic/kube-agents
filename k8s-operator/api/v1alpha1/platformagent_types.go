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
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// PlatformAgentSpec defines the desired state of PlatformAgent
type PlatformAgentSpec struct {
	// Harness configures the core execution environment and framework-level settings.
	// +optional
	Harness *HarnessSpec `json:"harness,omitempty"`

	// Deployment abstracts the Kubernetes Pod/Deployment configuration.
	// +optional
	Deployment *DeploymentSpec `json:"deployment,omitempty"`

	// Security configures RBAC, Pod Security, and Workload Identity.
	// +optional
	Security *SecuritySpec `json:"security,omitempty"`

	// Model configures the LLM reasoning backend.
	// +optional
	Model *ModelSpec `json:"model,omitempty"`

	// Integration configures platform-specific external connections.
	// +optional
	Integration *IntegrationSpec `json:"integration,omitempty"`
}

// HarnessSpec configures the core execution environment and framework-level settings for the agent.
// This extracts environmental context that doesn't belong in infrastructure blocks.
type HarnessSpec struct {
	// ClusterName is the logical name of the cluster where the agent is running.
	// +optional
	ClusterName string `json:"clusterName,omitempty"`

	// Location is the geographical location or cloud region.
	// +optional
	Location string `json:"location,omitempty"`

	// Hermes configures the internal event-routing or agent framework.
	// +optional
	Hermes *HermesSpec `json:"hermes,omitempty"`
}

type HermesSpec struct {
	// DashboardEnabled toggles the PLATFORM_AGENT_DASHBOARD environment variable.
	// +kubebuilder:default=true
	// +optional
	DashboardEnabled *bool `json:"dashboardEnabled,omitempty"`

	// PluginsDebug toggles the PLATFORM_AGENT_PLUGINS_DEBUG environment variable.
	// +kubebuilder:default=false
	// +optional
	PluginsDebug *bool `json:"pluginsDebug,omitempty"`

	// PlatformAgentHome is the path to the PLATFORM_AGENT_HOME directory.
	// +kubebuilder:default="/opt/data"
	// +optional
	PlatformAgentHome string `json:"platformAgentHome,omitempty"`

	// ApiServerSecretRef securely references a Secret containing the API_SERVER_KEY.
	// +optional
	ApiServerSecretRef *corev1.SecretKeySelector `json:"apiServerSecretRef,omitempty"`
}

// DeploymentSpec abstracts the Kubernetes Pod/Deployment configuration,
// completely decoupling the compute payload from the agent's application logic.
type DeploymentSpec struct {
	// Image specifies the container image repository.
	// +required
	Image string `json:"image"`

	// Tag specifies the container image tag.
	// +kubebuilder:default="latest"
	// +optional
	Tag *string `json:"tag,omitempty"`

	// ImagePullPolicy specifies if the image should be pulled.
	// +kubebuilder:default=IfNotPresent
	// +optional
	ImagePullPolicy *corev1.PullPolicy `json:"imagePullPolicy,omitempty"`
}

// SecuritySpec manages Kubernetes RBAC, Pod Security, and Cloud Workload Identity,
// decoupling the operator from being strictly tied to GCP.
type SecuritySpec struct {
	// ServiceAccountName is the Kubernetes Service Account bound to the Deployment.
	// +optional
	ServiceAccountName string `json:"serviceAccountName,omitempty"`

	// WorkloadIdentity maps external IAM to the KSA securely.
	// +optional
	WorkloadIdentity *WorkloadIdentitySpec `json:"workloadIdentity,omitempty"`
}

// WorkloadIdentitySpec maps external IAM to the KSA securely.
type WorkloadIdentitySpec struct {
	// Gcp configures Google Cloud Workload Identity.
	// +optional
	Gcp *GcpWorkloadIdentitySpec `json:"gcp,omitempty"`
}

// GcpWorkloadIdentitySpec configures Google Cloud Workload Identity.
type GcpWorkloadIdentitySpec struct {
	// GSAName is the Google Service Account Name.
	// +optional
	GSAName string `json:"gsaName,omitempty"`

	// ProjectID is the GCP Project ID mapping the GSA to the KSA.
	// +optional
	ProjectID string `json:"projectId,omitempty"`
}

// ModelSpec configures the LLM reasoning backend. By utilizing nested providers,
// the AI provider is abstracted away from the core deployment logic.
type ModelSpec struct {
	// Provider is the active AI provider (e.g., "gemini").
	// +kubebuilder:validation:MinLength=1
	Provider string `json:"provider"`

	// Default is the primary model to use (e.g., "gemini-3.1-flash-lite").
	// +kubebuilder:validation:MinLength=1
	Default string `json:"default"`

	// Gemini configures the Gemini provider.
	// +optional
	Gemini *GeminiSpec `json:"gemini,omitempty"`
}

type GeminiSpec struct {
	// ApiKeySecretRef securely references a Secret containing the GEMINI_API_KEY.
	// +optional
	ApiKeySecretRef *corev1.SecretKeySelector `json:"apiKeySecretRef,omitempty"`
}

// IntegrationSpec isolates platform-specific external connections.
type IntegrationSpec struct {
	// GoogleChat configures the Google Chat integration.
	// +optional
	GoogleChat *GoogleChatSpec `json:"googleChat,omitempty"`
}

// GoogleChatSpec contains the configuration for the Google Chat integration,
// enabling communication and event routing via Google Chat.
type GoogleChatSpec struct {
	// Enabled toggles the Google Chat integration.
	// +kubebuilder:default=false
	// +optional
	Enabled *bool `json:"enabled,omitempty"`

	// ProjectID is the target GCP Project ID for Pub/Sub.
	// +optional
	ProjectID string `json:"projectId,omitempty"`

	// TopicName is the GCP Chat Topic Name.
	// +optional
	TopicName string `json:"topicName,omitempty"`

	// SubscriptionName is the GCP Chat Subscription Name.
	// +optional
	SubscriptionName string `json:"subscriptionName,omitempty"`

	// AllowedUsers is a list of allowed users. If not present, all users will be allowed.
	// +optional
	AllowedUsers []string `json:"allowedUsers,omitempty"`

	// HomeChannel is the home channel Chat address.
	// +optional
	HomeChannel string `json:"homeChannel,omitempty"`
}

// PlatformAgentStatus defines the observed state of PlatformAgent.
type PlatformAgentStatus struct {
	Phase string `json:"phase,omitempty"` // e.g., "Provisioning", "Ready"
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status

// PlatformAgent is the Schema for the platformagents API
type PlatformAgent struct {
	metav1.TypeMeta `json:",inline"`

	// metadata is a standard object metadata
	// +optional
	metav1.ObjectMeta `json:"metadata,omitempty"`

	// spec defines the desired state of PlatformAgent
	// +required
	Spec PlatformAgentSpec `json:"spec"`

	// status defines the observed state of PlatformAgent
	// +optional
	Status PlatformAgentStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// PlatformAgentList contains a list of PlatformAgent
type PlatformAgentList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitzero"`
	Items           []PlatformAgent `json:"items"`
}

func init() {
	SchemeBuilder.Register(&PlatformAgent{}, &PlatformAgentList{})
}
