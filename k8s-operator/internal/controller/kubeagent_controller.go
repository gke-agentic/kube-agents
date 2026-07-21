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

package controller

import (
	"context"
	"fmt"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	logf "sigs.k8s.io/controller-runtime/pkg/log"

	agentv1alpha1 "github.com/gke-labs/kube-agents/k8s-operator/api/v1alpha1"
)

const kubeAgentFinalizer = "kubeagents.x-k8s.io/finalizer"

// KubeAgentReconciler reconciles a KubeAgent object
type KubeAgentReconciler struct {
	client.Client
	Scheme *runtime.Scheme
}

// +kubebuilder:rbac:groups=kubeagents.x-k8s.io,resources=kubeagents,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=kubeagents.x-k8s.io,resources=kubeagents/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=kubeagents.x-k8s.io,resources=kubeagents/finalizers,verbs=update
// +kubebuilder:rbac:groups=apps,resources=deployments,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups="",resources=serviceaccounts;persistentvolumeclaims;configmaps;services,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=rbac.authorization.k8s.io,resources=clusterroles;clusterrolebindings;roles;rolebindings,verbs=get;list;watch;create;update;patch;delete;bind

func (r *KubeAgentReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	log := logf.FromContext(ctx)

	instance := &agentv1alpha1.KubeAgent{}
	if err := r.Get(ctx, req.NamespacedName, instance); err != nil {
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}

	log.Info("Reconciling KubeAgent", "name", instance.Name, "namespace", instance.Namespace)

	// 1. Handle deletion
	if !instance.ObjectMeta.DeletionTimestamp.IsZero() {
		return r.handleDeletion(ctx, instance)
	}

	// 2. Ensure finalizer
	if !controllerutil.ContainsFinalizer(instance, kubeAgentFinalizer) {
		controllerutil.AddFinalizer(instance, kubeAgentFinalizer)
		if err := r.Update(ctx, instance); err != nil {
			return ctrl.Result{}, err
		}
		return ctrl.Result{}, nil
	}

	// 3. Reconcile ServiceAccount
	saName := instance.Name
	if instance.Spec.Security != nil && instance.Spec.Security.ServiceAccountName != "" {
		saName = instance.Spec.Security.ServiceAccountName
	}
	var saAnnotations map[string]string
	if instance.Spec.Security != nil {
		saAnnotations = instance.Spec.Security.ServiceAccountAnnotations
	}
	if err := ReconcileServiceAccount(ctx, r.Client, r.Scheme, instance, saName, instance.Namespace, saAnnotations, "kubeagent-controller"); err != nil {
		return ctrl.Result{}, err
	}

	// 4. Reconcile multi-namespace RBAC across spec.namespaces
	if err := r.reconcileRBAC(ctx, instance); err != nil {
		return ctrl.Result{}, err
	}

	// 5. Reconcile PVC
	if err := r.reconcilePVC(ctx, instance); err != nil {
		return ctrl.Result{}, err
	}

	// 6. Reconcile Workspace and Schedule ConfigMaps
	workspaceHash, err := r.reconcileWorkspaceConfigMap(ctx, instance)
	if err != nil {
		return ctrl.Result{}, err
	}

	scheduleHash, err := r.reconcileScheduleConfigMap(ctx, instance)
	if err != nil {
		return ctrl.Result{}, err
	}

	// 7. Reconcile Deployment
	if err := r.reconcileDeployment(ctx, instance, workspaceHash, scheduleHash); err != nil {
		return ctrl.Result{}, err
	}

	// 8. Reconcile Service
	if err := r.reconcileService(ctx, instance); err != nil {
		return ctrl.Result{}, err
	}

	// 9. Update Status
	if err := r.updateStatus(ctx, instance); err != nil {
		return ctrl.Result{}, err
	}

	return ctrl.Result{RequeueAfter: 30 * time.Second}, nil
}

func (r *KubeAgentReconciler) handleDeletion(ctx context.Context, agent *agentv1alpha1.KubeAgent) (ctrl.Result, error) {
	if !controllerutil.ContainsFinalizer(agent, kubeAgentFinalizer) {
		return ctrl.Result{}, nil
	}

	// Clean up cluster-scoped RBAC if present
	roleName := fmt.Sprintf("kubeagents:kubeagent:%s:%s", agent.Namespace, agent.Name)
	crb := &rbacv1.ClusterRoleBinding{
		ObjectMeta: metav1.ObjectMeta{Name: roleName},
	}
	_ = r.Delete(ctx, crb)

	cr := &rbacv1.ClusterRole{
		ObjectMeta: metav1.ObjectMeta{Name: roleName},
	}
	_ = r.Delete(ctx, cr)

	controllerutil.RemoveFinalizer(agent, kubeAgentFinalizer)
	if err := r.Update(ctx, agent); err != nil {
		return ctrl.Result{}, err
	}
	return ctrl.Result{}, nil
}

func (r *KubeAgentReconciler) reconcileRBAC(ctx context.Context, agent *agentv1alpha1.KubeAgent) error {
	if hasClusterWidePermissions(agent.Spec.Namespaces) || len(agent.Spec.Namespaces) == 0 {
		cr := buildKubeAgentClusterRole(agent)
		if err := r.Patch(ctx, cr, client.Apply, client.ForceOwnership, client.FieldOwner("kubeagent-controller")); err != nil {
			return err
		}

		crb := buildKubeAgentClusterRoleBinding(agent)
		if err := r.Patch(ctx, crb, client.Apply, client.ForceOwnership, client.FieldOwner("kubeagent-controller")); err != nil {
			return err
		}
		return nil
	}

	for _, targetNs := range agent.Spec.Namespaces {
		role := buildKubeAgentRole(agent, targetNs)
		if err := r.Patch(ctx, role, client.Apply, client.ForceOwnership, client.FieldOwner("kubeagent-controller")); err != nil {
			return err
		}

		rb := buildKubeAgentRoleBinding(agent, targetNs)
		if err := r.Patch(ctx, rb, client.Apply, client.ForceOwnership, client.FieldOwner("kubeagent-controller")); err != nil {
			return err
		}
	}

	return nil
}

func (r *KubeAgentReconciler) reconcilePVC(ctx context.Context, agent *agentv1alpha1.KubeAgent) error {
	pvc := buildKubeAgentPVC(agent)
	if err := controllerutil.SetControllerReference(agent, pvc, r.Scheme); err != nil {
		return err
	}

	existing := &corev1.PersistentVolumeClaim{}
	err := r.Get(ctx, types.NamespacedName{Name: pvc.Name, Namespace: pvc.Namespace}, existing)
	if errors.IsNotFound(err) {
		return r.Create(ctx, pvc)
	}
	return err
}

func (r *KubeAgentReconciler) reconcileWorkspaceConfigMap(ctx context.Context, agent *agentv1alpha1.KubeAgent) (string, error) {
	cm := buildKubeAgentWorkspaceConfigMap(agent)
	if err := controllerutil.SetControllerReference(agent, cm, r.Scheme); err != nil {
		return "", err
	}
	if err := r.Patch(ctx, cm, client.Apply, client.ForceOwnership, client.FieldOwner("kubeagent-controller")); err != nil {
		return "", err
	}
	return hashConfigMapData(cm), nil
}

func (r *KubeAgentReconciler) reconcileScheduleConfigMap(ctx context.Context, agent *agentv1alpha1.KubeAgent) (string, error) {
	cm := buildKubeAgentScheduleConfigMap(agent)
	if err := controllerutil.SetControllerReference(agent, cm, r.Scheme); err != nil {
		return "", err
	}
	if err := r.Patch(ctx, cm, client.Apply, client.ForceOwnership, client.FieldOwner("kubeagent-controller")); err != nil {
		return "", err
	}
	return hashConfigMapData(cm), nil
}

func (r *KubeAgentReconciler) reconcileDeployment(ctx context.Context, agent *agentv1alpha1.KubeAgent, workspaceHash, scheduleHash string) error {
	dep := buildKubeAgentDeployment(agent, workspaceHash, scheduleHash)
	if err := controllerutil.SetControllerReference(agent, dep, r.Scheme); err != nil {
		return err
	}
	return r.Patch(ctx, dep, client.Apply, client.ForceOwnership, client.FieldOwner("kubeagent-controller"))
}

func (r *KubeAgentReconciler) reconcileService(ctx context.Context, agent *agentv1alpha1.KubeAgent) error {
	svc := buildKubeAgentService(agent)
	if err := controllerutil.SetControllerReference(agent, svc, r.Scheme); err != nil {
		return err
	}
	return r.Patch(ctx, svc, client.Apply, client.ForceOwnership, client.FieldOwner("kubeagent-controller"))
}

func (r *KubeAgentReconciler) updateStatus(ctx context.Context, agent *agentv1alpha1.KubeAgent) error {
	dep := &appsv1.Deployment{}
	err := r.Get(ctx, types.NamespacedName{Name: agent.Name, Namespace: agent.Namespace}, dep)
	if err != nil && !errors.IsNotFound(err) {
		return err
	}

	phase := "Provisioning"
	if dep.Status.ReadyReplicas > 0 {
		phase = "Ready"
	}

	now := metav1.Now()
	agent.Status.Phase = phase
	agent.Status.LastReconcileTime = &now
	agent.Status.DeploymentStatus.Name = agent.Name
	agent.Status.DeploymentStatus.ReadyReplicas = dep.Status.ReadyReplicas
	agent.Status.ServiceStatus.Endpoint = fmt.Sprintf("http://%s.%s.svc.cluster.local:8080", agent.Name, agent.Namespace)

	return r.Status().Update(ctx, agent)
}

// SetupWithManager sets up the controller with the Manager.
func (r *KubeAgentReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&agentv1alpha1.KubeAgent{}).
		Owns(&appsv1.Deployment{}).
		Owns(&corev1.Service{}).
		Owns(&corev1.ConfigMap{}).
		Complete(r)
}
