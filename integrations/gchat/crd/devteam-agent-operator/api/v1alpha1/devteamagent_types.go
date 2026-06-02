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

// EDIT THIS FILE!  THIS IS SCAFFOLDING FOR YOU TO OWN!
// NOTE: json tags are required.  Any new fields you add must have json tags for the fields to be serialized.

// DevTeamAgentSpec defines the desired state of DevTeamAgent
type DevTeamAgentSpec struct {
	// ImageURI is the container image for the devteam agent.
	ImageURI string `json:"imageUri"`

	// Replicas is the number of desired pods. Defaults to 1.
	// +optional
	// +kubebuilder:default=1
	Replicas *int32 `json:"replicas,omitempty"`

	// StorageSize is the size of the persistent volume claim. Defaults to "10Gi".
	// +optional
	// +kubebuilder:default="10Gi"
	StorageSize string `json:"storageSize,omitempty"`

	// --- Model Config (AI) ---

	// ModelName is the name of the Gemini model to use. Defaults to "gemini-model".
	// +optional
	// +kubebuilder:default="gemini-model"
	ModelName string `json:"modelName,omitempty"`

	// ModelBaseURL is the base URL for the model API. Defaults to "http://litellm.agent-system.svc.cluster.local/v1".
	// +optional
	// +kubebuilder:default="http://litellm.agent-system.svc.cluster.local/v1"
	ModelBaseURL string `json:"modelBaseUrl,omitempty"`

	// ModelAPIKey is the API key for the model. Defaults to "none".
	// +optional
	// +kubebuilder:default="none"
	ModelAPIKey string `json:"modelApiKey,omitempty"`

	// ApiServerKeySecretRef is a reference to the Secret containing the API server key.
	// The secret must contain a key named "api-server-key".
	// Defaults to "devteam-agent-secrets".
	// +optional
	// +kubebuilder:default="devteam-agent-secrets"
	ApiServerKeySecretRef string `json:"apiServerKeySecretRef,omitempty"`

	// --- GCP / GKE Context ---

	// ProjectID is the target GCP Project ID.
	// +optional
	ProjectID string `json:"projectId,omitempty"`

	// NumericProjectID is the target GCP Project Number.
	// +optional
	NumericProjectID string `json:"numericProjectId,omitempty"`

	// ClusterName is the host GKE Cluster Name.
	// +optional
	ClusterName string `json:"clusterName,omitempty"`

	// Location is the host GKE Cluster Location.
	// +optional
	Location string `json:"location,omitempty"`

	// --- Identity (Workload Identity) ---

	// GSAName is the GCP Service Account Name to bind to.
	// If provided, the operator will annotate the KSA with this GSA to enable Workload Identity.
	// +optional
	GSAName string `json:"gsaName,omitempty"`

	// KSAName is the Kubernetes Service Account Name. Defaults to the CR name.
	// +optional
	KSAName string `json:"ksaName,omitempty"`
}

// DevTeamAgentStatus defines the observed state of DevTeamAgent.
type DevTeamAgentStatus struct {
	// Phase represents the current phase of the agent (e.g., Provisioning, Ready, Failed).
	// +optional
	Phase string `json:"phase,omitempty"`

	// conditions represent the current state of the DevTeamAgent resource.
	// +listType=map
	// +listMapKey=type
	// +optional
	Conditions []metav1.Condition `json:"conditions,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status

// DevTeamAgent is the Schema for the devteamagents API
type DevTeamAgent struct {
	metav1.TypeMeta `json:",inline"`

	// metadata is a standard object metadata
	// +optional
	metav1.ObjectMeta `json:"metadata,omitzero"`

	// spec defines the desired state of DevTeamAgent
	// +required
	Spec DevTeamAgentSpec `json:"spec"`

	// status defines the observed state of DevTeamAgent
	// +optional
	Status DevTeamAgentStatus `json:"status,omitzero"`
}

// +kubebuilder:object:root=true

// DevTeamAgentList contains a list of DevTeamAgent
type DevTeamAgentList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitzero"`
	Items           []DevTeamAgent `json:"items"`
}

func init() {
	SchemeBuilder.Register(&DevTeamAgent{}, &DevTeamAgentList{})
}
