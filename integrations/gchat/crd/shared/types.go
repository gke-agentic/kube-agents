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

package shared

// +kubebuilder:object:generate=true

// CommonAgentSpec defines the common desired state for agents.
// This struct can be embedded in custom resource specs to reuse fields.
type CommonAgentSpec struct {
	// ImageURI is the container image for the agent.
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
