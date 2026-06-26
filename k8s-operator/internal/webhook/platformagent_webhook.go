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

package webhook

import (
	"context"
	"fmt"

	coordinationv1 "k8s.io/api/coordination/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/util/validation/field"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
	"sigs.k8s.io/controller-runtime/pkg/webhook/admission"

	agentv1alpha1 "github.com/gke-labs/kube-agents/k8s-operator/api/v1alpha1"
	"github.com/gke-labs/kube-agents/k8s-operator/internal/controller"
)

// log is for logging in this package.
var platformagentlog = logf.Log.WithName("platformagent-resource")

// SetupPlatformAgentWebhookWithManager registers the webhook for PlatformAgent in the manager.
func SetupPlatformAgentWebhookWithManager(mgr ctrl.Manager) error {
	return ctrl.NewWebhookManagedBy(mgr).
		For(&agentv1alpha1.PlatformAgent{}).
		WithDefaulter(&PlatformAgentCustomDefaulter{}).
		WithValidator(&PlatformAgentCustomValidator{
			Client: mgr.GetAPIReader(),
		}).
		Complete()
}

// +kubebuilder:webhook:path=/mutate-kubeagents-x-k8s-io-v1alpha1-platformagent,mutating=true,failurePolicy=fail,sideEffects=None,groups=kubeagents.x-k8s.io,resources=platformagents,verbs=create;update,versions=v1alpha1,name=mplatformagent.kb.io,admissionReviewVersions=v1

// PlatformAgentCustomDefaulter struct to implement CustomDefaulter.
type PlatformAgentCustomDefaulter struct {
	// TODO(user): Add fields if needed
}

var _ admission.CustomDefaulter = &PlatformAgentCustomDefaulter{}

// Default implements admission.CustomDefaulter so a webhook will be registered for the type PlatformAgent.
func (d *PlatformAgentCustomDefaulter) Default(ctx context.Context, obj runtime.Object) error {
	platformAgent, ok := obj.(*agentv1alpha1.PlatformAgent)
	if !ok {
		return fmt.Errorf("expected a PlatformAgent object but got %T", obj)
	}
	platformagentlog.Info("defaulting PlatformAgent", "name", platformAgent.Name)

	// TODO(user): fill in defaulting logic here

	return nil
}

// +kubebuilder:webhook:path=/validate-kubeagents-x-k8s-io-v1alpha1-platformagent,mutating=false,failurePolicy=fail,sideEffects=None,groups=kubeagents.x-k8s.io,resources=platformagents,verbs=create;update;delete,versions=v1alpha1,name=vplatformagent.kb.io,admissionReviewVersions=v1

// PlatformAgentCustomValidator struct to implement CustomValidator.
type PlatformAgentCustomValidator struct {
	Client client.Reader
}

var _ admission.CustomValidator = &PlatformAgentCustomValidator{}

// ValidateCreate implements admission.CustomValidator so a webhook will be registered for the type PlatformAgent.
func (v *PlatformAgentCustomValidator) ValidateCreate(ctx context.Context, obj runtime.Object) (admission.Warnings, error) {
	platformAgent, ok := obj.(*agentv1alpha1.PlatformAgent)
	if !ok {
		return nil, fmt.Errorf("expected a PlatformAgent object but got %T", obj)
	}
	platformagentlog.Info("validating PlatformAgent creation", "name", platformAgent.Name)

	return v.validatePlatformAgent(ctx, platformAgent)
}

// ValidateUpdate implements admission.CustomValidator so a webhook will be registered for the type PlatformAgent.
func (v *PlatformAgentCustomValidator) ValidateUpdate(ctx context.Context, oldObj, newObj runtime.Object) (admission.Warnings, error) {
	platformAgent, ok := newObj.(*agentv1alpha1.PlatformAgent)
	if !ok {
		return nil, fmt.Errorf("expected a PlatformAgent object but got %T", newObj)
	}
	platformagentlog.Info("validating PlatformAgent update", "name", platformAgent.Name)

	return v.validatePlatformAgent(ctx, platformAgent)
}

func (v *PlatformAgentCustomValidator) validatePlatformAgent(ctx context.Context, platformAgent *agentv1alpha1.PlatformAgent) (admission.Warnings, error) {
	// Skip validation for terminating agents to avoid deadlocks during deletion (e.g. finalizer removal)
	if platformAgent.DeletionTimestamp != nil {
		return nil, nil
	}

	// 1. Enforce 1 PlatformAgent per cluster limit
	if v.Client != nil {
		var list agentv1alpha1.PlatformAgentList
		if err := v.Client.List(ctx, &list); err != nil {
			return nil, err
		}
		for _, item := range list.Items {
			// Skip terminating agents to prevent deadlocking new platformagent deployment
			if item.DeletionTimestamp != nil {
				continue
			}
			if item.Name != platformAgent.Name || item.Namespace != platformAgent.Namespace {
				return nil, apierrors.NewInvalid(
					schema.GroupKind{Group: "kubeagents.x-k8s.io", Kind: "PlatformAgent"},
					platformAgent.Name,
					field.ErrorList{field.Forbidden(field.NewPath(""), "only one PlatformAgent is allowed per cluster")},
				)
			}
		}
	}

	// 2. Enforce 1 PlatformAgent per project globally (using Kubernetes Lease API)
	if v.Client != nil {
		projectID := controller.GetProjectID(platformAgent)
		if projectID == "" {
			return nil, apierrors.NewInvalid(
				schema.GroupKind{Group: "kubeagents.x-k8s.io", Kind: "PlatformAgent"},
				platformAgent.Name,
				field.ErrorList{field.Required(field.NewPath("spec", "harness", "projectId"), "GCP Project ID is required for global cardinality lock validation")},
			)
		}

		currentCluster := ""
		if platformAgent.Spec.Harness != nil {
			currentCluster = platformAgent.Spec.Harness.ClusterName
		}
		if currentCluster == "" {
			return nil, apierrors.NewInvalid(
				schema.GroupKind{Group: "kubeagents.x-k8s.io", Kind: "PlatformAgent"},
				platformAgent.Name,
				field.ErrorList{field.Required(field.NewPath("spec", "harness", "clusterName"), "clusterName is required when global cardinality lock is enabled")},
			)
		}

		// 2a. First, verify the lease in the local cluster (fast check)
		leaseName := fmt.Sprintf("platform-agent-lock-%s", projectID)
		leaseNamespace := "kube-system" // Using constant well-known namespace across all clusters

		lease := &coordinationv1.Lease{}
		err := v.Client.Get(ctx, client.ObjectKey{Name: leaseName, Namespace: leaseNamespace}, lease)
		if err == nil {
			if lease.Spec.HolderIdentity != nil {
				holder := *lease.Spec.HolderIdentity
				expectedHolder := fmt.Sprintf("%s/%s/%s", currentCluster, platformAgent.Namespace, platformAgent.Name)
				if holder != expectedHolder {
					return nil, apierrors.NewInvalid(
						schema.GroupKind{Group: "kubeagents.x-k8s.io", Kind: "PlatformAgent"},
						platformAgent.Name,
						field.ErrorList{field.Forbidden(field.NewPath(""), fmt.Sprintf("only one PlatformAgent is allowed per project; already running in GKE cluster (holder: %q)", holder))},
					)
				}
			}
		} else if !apierrors.IsNotFound(err) {
			return nil, apierrors.NewInternalError(fmt.Errorf("failed to verify project-level cardinality lease: %w", err))
		}

		// 2b. Query Google GKE API to discover all GKE clusters in this project.
		clusters, err := controller.ListGKEClusters(ctx, projectID)
		if err != nil {
			// If we fail to list clusters (e.g. running in testing without GCP API mocking), log the warning but do not block local operation
			platformagentlog.Error(err, "unable to list GKE clusters for global cardinality check")
		} else {
			for _, cluster := range clusters {
				// Skip the current cluster to avoid querying ourselves (match by name)
				if cluster.Name == currentCluster {
					continue
				}
				// Skip clusters that are not RUNNING
				if cluster.Status != "RUNNING" {
					continue
				}

				platformagentlog.Info("Checking remote GKE cluster for platform agent lease", "cluster", cluster.Name, "location", cluster.Location)

				// Build client for the remote cluster
				remoteClient, err := controller.BuildRemoteClientDynamically(ctx, projectID, cluster.Location, cluster.Name)
				if err != nil {
					// Log the error and continue (e.g. if we don't have access or network connectivity to that GKE master endpoint)
					platformagentlog.Error(err, "unable to connect to remote GKE cluster for cardinality check", "cluster", cluster.Name)
					continue
				}

				// Query the remote cluster for the Lease
				remoteLease := &coordinationv1.Lease{}
				err = remoteClient.Get(ctx, client.ObjectKey{Name: leaseName, Namespace: leaseNamespace}, remoteLease)
				if err == nil {
					if remoteLease.Spec.HolderIdentity != nil {
						holder := *remoteLease.Spec.HolderIdentity
						return nil, apierrors.NewInvalid(
							schema.GroupKind{Group: "kubeagents.x-k8s.io", Kind: "PlatformAgent"},
							platformAgent.Name,
							field.ErrorList{field.Forbidden(field.NewPath(""), fmt.Sprintf("only one PlatformAgent is allowed per project; already running in remote GKE cluster %s (holder: %q)", cluster.Name, holder))},
						)
					}
				} else if !apierrors.IsNotFound(err) {
					platformagentlog.Error(err, "failed to query lease on remote GKE cluster", "cluster", cluster.Name)
				}
			}
		}
	}

	return nil, nil
}

// ValidateDelete implements admission.CustomValidator so a webhook will be registered for the type PlatformAgent.
func (v *PlatformAgentCustomValidator) ValidateDelete(ctx context.Context, obj runtime.Object) (admission.Warnings, error) {
	platformAgent, ok := obj.(*agentv1alpha1.PlatformAgent)
	if !ok {
		return nil, fmt.Errorf("expected a PlatformAgent object but got %T", obj)
	}
	platformagentlog.Info("validating PlatformAgent deletion", "name", platformAgent.Name)

	return nil, nil
}
