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

	coordinationv1 "k8s.io/api/coordination/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	agentv1alpha1 "github.com/gke-labs/kube-agents/k8s-operator/api/v1alpha1"
)

func TestPlatformAgentValidation(t *testing.T) {
	ctx := context.Background()

	validSpec := agentv1alpha1.PlatformAgentSpec{
		Harness: &agentv1alpha1.PlatformAgentHarnessSpec{
			ProjectID:   "my-project",
			ClusterName: "my-cluster",
		},
	}

	t.Run("fails if another platform agent already exists in the cluster", func(t *testing.T) {
		existingAgent := &agentv1alpha1.PlatformAgent{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "existing-agent",
				Namespace: "kubeagents-system",
			},
			Spec: validSpec,
		}

		scheme := runtime.NewScheme()
		_ = agentv1alpha1.AddToScheme(scheme)
		_ = coordinationv1.AddToScheme(scheme)
		fakeClient := fake.NewClientBuilder().WithScheme(scheme).WithObjects(existingAgent).Build()

		val := &PlatformAgentCustomValidator{
			Client: fakeClient,
		}

		newAgent := &agentv1alpha1.PlatformAgent{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "new-agent",
				Namespace: "default",
			},
			Spec: validSpec,
		}

		_, err := val.ValidateCreate(ctx, newAgent)
		if err == nil {
			t.Error("expected validation to fail when another PlatformAgent already exists in the cluster")
		}
	})

	t.Run("allows creation when existing platform agent is terminating", func(t *testing.T) {
		now := metav1.Now()
		existingAgent := &agentv1alpha1.PlatformAgent{
			ObjectMeta: metav1.ObjectMeta{
				Name:              "existing-agent",
				Namespace:         "kubeagents-system",
				DeletionTimestamp: &now,
				Finalizers:        []string{"kubeagents.x-k8s.io/platformagent-webhook-lock"},
			},
			Spec: validSpec,
		}

		scheme := runtime.NewScheme()
		_ = agentv1alpha1.AddToScheme(scheme)
		_ = coordinationv1.AddToScheme(scheme)
		fakeClient := fake.NewClientBuilder().WithScheme(scheme).WithObjects(existingAgent).Build()

		val := &PlatformAgentCustomValidator{
			Client: fakeClient,
		}

		newAgent := &agentv1alpha1.PlatformAgent{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "new-agent",
				Namespace: "default",
			},
			Spec: validSpec,
		}

		_, err := val.ValidateCreate(ctx, newAgent)
		if err != nil {
			t.Errorf("unexpected validation failure: %v", err)
		}
	})

	t.Run("allows update to the same existing platform agent", func(t *testing.T) {
		existingAgent := &agentv1alpha1.PlatformAgent{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "existing-agent",
				Namespace: "kubeagents-system",
			},
			Spec: validSpec,
		}

		scheme := runtime.NewScheme()
		_ = agentv1alpha1.AddToScheme(scheme)
		_ = coordinationv1.AddToScheme(scheme)
		fakeClient := fake.NewClientBuilder().WithScheme(scheme).WithObjects(existingAgent).Build()

		val := &PlatformAgentCustomValidator{
			Client: fakeClient,
		}

		_, err := val.ValidateUpdate(ctx, nil, existingAgent)
		if err != nil {
			t.Errorf("unexpected error when updating the same existing PlatformAgent: %v", err)
		}
	})

	t.Run("allows creation when global lease lock does not exist", func(t *testing.T) {
		scheme := runtime.NewScheme()
		_ = agentv1alpha1.AddToScheme(scheme)
		_ = coordinationv1.AddToScheme(scheme)
		fakeClient := fake.NewClientBuilder().WithScheme(scheme).Build()

		val := &PlatformAgentCustomValidator{
			Client: fakeClient,
		}

		agent := &agentv1alpha1.PlatformAgent{
			ObjectMeta: metav1.ObjectMeta{Name: "test-agent", Namespace: "default"},
			Spec:       validSpec,
		}

		_, err := val.ValidateCreate(ctx, agent)
		if err != nil {
			t.Errorf("unexpected validation failure: %v", err)
		}
	})

	t.Run("fails when global lease lock is held by a different GKE cluster", func(t *testing.T) {
		holder := "different-cluster/default/another-agent"
		lease := &coordinationv1.Lease{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "platform-agent-lock-my-project",
				Namespace: "default",
			},
			Spec: coordinationv1.LeaseSpec{
				HolderIdentity: &holder,
			},
		}

		scheme := runtime.NewScheme()
		_ = agentv1alpha1.AddToScheme(scheme)
		_ = coordinationv1.AddToScheme(scheme)
		fakeClient := fake.NewClientBuilder().WithScheme(scheme).WithObjects(lease).Build()

		val := &PlatformAgentCustomValidator{
			Client: fakeClient,
		}

		agent := &agentv1alpha1.PlatformAgent{
			ObjectMeta: metav1.ObjectMeta{Name: "test-agent", Namespace: "default"},
			Spec:       validSpec,
		}

		_, err := val.ValidateCreate(ctx, agent)
		if err == nil {
			t.Error("expected validation to fail since lock is held by another cluster")
		}
	})

	t.Run("allows creation when global lease lock belongs to the same cluster, agent, and namespace", func(t *testing.T) {
		holder := "my-cluster/kubeagents-system/test-agent"
		lease := &coordinationv1.Lease{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "platform-agent-lock-my-project",
				Namespace: "kubeagents-system",
			},
			Spec: coordinationv1.LeaseSpec{
				HolderIdentity: &holder,
			},
		}

		scheme := runtime.NewScheme()
		_ = agentv1alpha1.AddToScheme(scheme)
		_ = coordinationv1.AddToScheme(scheme)
		// Need to also add the agent itself so cluster check passes
		agent := &agentv1alpha1.PlatformAgent{
			ObjectMeta: metav1.ObjectMeta{Name: "test-agent", Namespace: "kubeagents-system"},
			Spec:       validSpec,
		}

		fakeClient := fake.NewClientBuilder().WithScheme(scheme).WithObjects(lease).Build()

		val := &PlatformAgentCustomValidator{
			Client: fakeClient,
		}

		_, err := val.ValidateCreate(ctx, agent)
		if err != nil {
			t.Errorf("unexpected validation failure: %v", err)
		}
	})

	t.Run("fails when global lock check is active but clusterName is empty", func(t *testing.T) {
		scheme := runtime.NewScheme()
		_ = agentv1alpha1.AddToScheme(scheme)
		_ = coordinationv1.AddToScheme(scheme)
		fakeClient := fake.NewClientBuilder().WithScheme(scheme).Build()

		val := &PlatformAgentCustomValidator{
			Client: fakeClient,
		}

		agent := &agentv1alpha1.PlatformAgent{
			ObjectMeta: metav1.ObjectMeta{Name: "test-agent", Namespace: "default"},
			Spec: agentv1alpha1.PlatformAgentSpec{
				Integration: &agentv1alpha1.IntegrationSpec{
					GoogleChat: &agentv1alpha1.GoogleChatSpec{ProjectID: "my-project"},
				},
				Harness: &agentv1alpha1.PlatformAgentHarnessSpec{ClusterName: ""},
			},
		}

		_, err := val.ValidateCreate(ctx, agent)
		if err == nil {
			t.Error("expected validation to fail when clusterName is empty")
		}
	})

	t.Run("fails when global lock check is active but projectID is empty", func(t *testing.T) {
		scheme := runtime.NewScheme()
		_ = agentv1alpha1.AddToScheme(scheme)
		_ = coordinationv1.AddToScheme(scheme)
		fakeClient := fake.NewClientBuilder().WithScheme(scheme).Build()

		val := &PlatformAgentCustomValidator{
			Client: fakeClient,
		}

		agent := &agentv1alpha1.PlatformAgent{
			ObjectMeta: metav1.ObjectMeta{Name: "test-agent", Namespace: "default"},
			Spec: agentv1alpha1.PlatformAgentSpec{
				Harness: &agentv1alpha1.PlatformAgentHarnessSpec{ClusterName: "my-cluster"},
			},
		}

		_, err := val.ValidateCreate(ctx, agent)
		if err == nil {
			t.Error("expected validation to fail when projectID is empty and lease check is active")
		}
	})
}
