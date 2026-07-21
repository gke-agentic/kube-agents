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

	"k8s.io/apimachinery/pkg/runtime"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
	"sigs.k8s.io/controller-runtime/pkg/webhook/admission"

	agentv1alpha1 "github.com/gke-labs/kube-agents/k8s-operator/api/v1alpha1"
)

var kubeagentlog = logf.Log.WithName("kubeagent-resource")

// SetupKubeAgentWebhookWithManager registers the webhook for KubeAgent in the manager.
func SetupKubeAgentWebhookWithManager(mgr ctrl.Manager) error {
	return ctrl.NewWebhookManagedBy(mgr).
		For(&agentv1alpha1.KubeAgent{}).
		WithDefaulter(&KubeAgentCustomDefaulter{}).
		WithValidator(&KubeAgentCustomValidator{
			Client: mgr.GetAPIReader(),
		}).
		Complete()
}

// +kubebuilder:webhook:path=/mutate-kubeagents-x-k8s-io-v1alpha1-kubeagent,mutating=true,failurePolicy=fail,sideEffects=None,groups=kubeagents.x-k8s.io,resources=kubeagents,verbs=create;update,versions=v1alpha1,name=mkubeagent.kb.io,admissionReviewVersions=v1

// KubeAgentCustomDefaulter struct to implement CustomDefaulter.
type KubeAgentCustomDefaulter struct{}

var _ admission.CustomDefaulter = &KubeAgentCustomDefaulter{}

// Default implements admission.CustomDefaulter.
func (d *KubeAgentCustomDefaulter) Default(ctx context.Context, obj runtime.Object) error {
	kubeAgent, ok := obj.(*agentv1alpha1.KubeAgent)
	if !ok {
		return fmt.Errorf("expected a KubeAgent object but got %T", obj)
	}
	kubeagentlog.Info("defaulting KubeAgent", "name", kubeAgent.Name)
	return nil
}

// +kubebuilder:webhook:path=/validate-kubeagents-x-k8s-io-v1alpha1-kubeagent,mutating=false,failurePolicy=fail,sideEffects=None,groups=kubeagents.x-k8s.io,resources=kubeagents,verbs=create;update;delete,versions=v1alpha1,name=vkubeagent.kb.io,admissionReviewVersions=v1

// KubeAgentCustomValidator struct to implement CustomValidator.
type KubeAgentCustomValidator struct {
	Client client.Reader
}

var _ admission.CustomValidator = &KubeAgentCustomValidator{}

// ValidateCreate implements admission.CustomValidator.
func (v *KubeAgentCustomValidator) ValidateCreate(ctx context.Context, obj runtime.Object) (admission.Warnings, error) {
	kubeAgent, ok := obj.(*agentv1alpha1.KubeAgent)
	if !ok {
		return nil, fmt.Errorf("expected a KubeAgent object but got %T", obj)
	}
	kubeagentlog.Info("validating KubeAgent creation", "name", kubeAgent.Name)
	return v.validateKubeAgent(ctx, kubeAgent)
}

// ValidateUpdate implements admission.CustomValidator.
func (v *KubeAgentCustomValidator) ValidateUpdate(ctx context.Context, oldObj, newObj runtime.Object) (admission.Warnings, error) {
	kubeAgent, ok := newObj.(*agentv1alpha1.KubeAgent)
	if !ok {
		return nil, fmt.Errorf("expected a KubeAgent object but got %T", newObj)
	}
	kubeagentlog.Info("validating KubeAgent update", "name", kubeAgent.Name)
	return v.validateKubeAgent(ctx, kubeAgent)
}

// ValidateDelete implements admission.CustomValidator.
func (v *KubeAgentCustomValidator) ValidateDelete(ctx context.Context, obj runtime.Object) (admission.Warnings, error) {
	return nil, nil
}

// validateKubeAgent enforces zero-cardinality constraints (unlimited concurrent specialized agents allowed).
func (v *KubeAgentCustomValidator) validateKubeAgent(ctx context.Context, kubeAgent *agentv1alpha1.KubeAgent) (admission.Warnings, error) {
	// Zero-cardinality enforcement: unlimited specialized standalone KubeAgent resources can run concurrently.
	return nil, nil
}
