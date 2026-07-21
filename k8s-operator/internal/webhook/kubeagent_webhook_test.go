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
	"testing"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	agentv1alpha1 "github.com/gke-labs/kube-agents/k8s-operator/api/v1alpha1"
)

func TestKubeAgentValidationZeroCardinality(t *testing.T) {
	ctx := context.Background()

	t.Run("allows multiple KubeAgents to exist concurrently (zero cardinality enforcement)", func(t *testing.T) {
		existingAgent1 := &agentv1alpha1.KubeAgent{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "agent-1",
				Namespace: "default",
			},
			Spec: agentv1alpha1.KubeAgentSpec{},
		}
		existingAgent2 := &agentv1alpha1.KubeAgent{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "agent-2",
				Namespace: "kubeagents",
			},
			Spec: agentv1alpha1.KubeAgentSpec{},
		}

		scheme := runtime.NewScheme()
		_ = agentv1alpha1.AddToScheme(scheme)
		fakeClient := fake.NewClientBuilder().WithScheme(scheme).WithObjects(existingAgent1, existingAgent2).Build()

		val := &KubeAgentCustomValidator{
			Client: fakeClient,
		}

		newAgent := &agentv1alpha1.KubeAgent{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "agent-3",
				Namespace: "default",
			},
			Spec: agentv1alpha1.KubeAgentSpec{},
		}

		if _, err := val.ValidateCreate(ctx, newAgent); err != nil {
			t.Errorf("expected ValidateCreate to succeed with existing agents, got: %v", err)
		}

		if _, err := val.ValidateUpdate(ctx, existingAgent1, newAgent); err != nil {
			t.Errorf("expected ValidateUpdate to succeed, got: %v", err)
		}
	})
}

func TestKubeAgentDefaulting(t *testing.T) {
	ctx := context.Background()

	t.Run("default succeeds on KubeAgent", func(t *testing.T) {
		defaulter := &KubeAgentCustomDefaulter{}
		agent := &agentv1alpha1.KubeAgent{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "test-agent",
				Namespace: "default",
			},
		}
		if err := defaulter.Default(ctx, agent); err != nil {
			t.Errorf("unexpected error defaulting KubeAgent: %v", err)
		}
	})
}
