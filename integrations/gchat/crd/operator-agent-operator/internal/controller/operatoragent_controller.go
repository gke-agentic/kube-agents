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
	"reflect"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/runtime"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	logf "sigs.k8s.io/controller-runtime/pkg/log"

	shared "github.com/gke-agentic/kube-agents/integrations/gchat/crd/shared"
	operatorv1alpha1 "github.com/gke-agentic/operator-agent-operator/api/v1alpha1"
)

// OperatorAgentReconciler reconciles a OperatorAgent object
type OperatorAgentReconciler struct {
	client.Client
	Scheme *runtime.Scheme
}

// +kubebuilder:rbac:groups=operator.platform.io,resources=operatoragents,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=operator.platform.io,resources=operatoragents/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=operator.platform.io,resources=operatoragents/finalizers,verbs=update
// +kubebuilder:rbac:groups=apps,resources=deployments,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups="",resources=persistentvolumeclaims;services;serviceaccounts,verbs=get;list;watch;create;update;patch;delete

// Reconcile is part of the main kubernetes reconciliation loop which aims to
// move the current state of the cluster closer to the desired state.
func (r *OperatorAgentReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	log := logf.FromContext(ctx)

	// 1. Fetch the OperatorAgent instance
	instance := &operatorv1alpha1.OperatorAgent{}
	err := r.Get(ctx, req.NamespacedName, instance)
	if err != nil {
		if errors.IsNotFound(err) {
			return ctrl.Result{}, nil
		}
		return ctrl.Result{}, err
	}

	log.Info("Reconciling OperatorAgent", "name", instance.Name)

	// Update status phase to Provisioning if empty
	if instance.Status.Phase == "" {
		instance.Status.Phase = "Provisioning"
		if err := r.Status().Update(ctx, instance); err != nil {
			return ctrl.Result{}, err
		}
		return ctrl.Result{}, nil
	}

	// 2. Reconcile ServiceAccount (Workload Identity)
	if err := r.reconcileKSA(ctx, instance); err != nil {
		log.Error(err, "Failed to reconcile ServiceAccount")
		return ctrl.Result{}, err
	}

	// 3. Reconcile PVC
	if err := r.reconcilePVC(ctx, instance); err != nil {
		log.Error(err, "Failed to reconcile PVC")
		return ctrl.Result{}, err
	}

	// 4. Reconcile Deployment
	if err := r.reconcileDeployment(ctx, instance); err != nil {
		log.Error(err, "Failed to reconcile Deployment")
		return ctrl.Result{}, err
	}

	// 5. Reconcile Service
	if err := r.reconcileService(ctx, instance); err != nil {
		log.Error(err, "Failed to reconcile Service")
		return ctrl.Result{}, err
	}

	// Update status phase to Ready
	if instance.Status.Phase != "Ready" {
		instance.Status.Phase = "Ready"
		if err := r.Status().Update(ctx, instance); err != nil {
			return ctrl.Result{}, err
		}
		log.Info("OperatorAgent is Ready", "name", instance.Name)
	}

	return ctrl.Result{}, nil
}

func (r *OperatorAgentReconciler) reconcileKSA(ctx context.Context, instance *operatorv1alpha1.OperatorAgent) error {
	ksaName := instance.Spec.KSAName
	if ksaName == "" {
		ksaName = instance.Name
	}

	ksa := shared.BuildKSA(ksaName, instance.Namespace, instance.Spec.GSAName, instance.Spec.ProjectID)

	if err := ctrl.SetControllerReference(instance, ksa, r.Scheme); err != nil {
		return err
	}

	found := &corev1.ServiceAccount{}
	err := r.Get(ctx, client.ObjectKey{Name: ksa.Name, Namespace: ksa.Namespace}, found)
	if err != nil {
		if errors.IsNotFound(err) {
			return r.Create(ctx, ksa)
		}
		return err
	}

	if !reflect.DeepEqual(found.Annotations, ksa.Annotations) {
		found.Annotations = ksa.Annotations
		return r.Update(ctx, found)
	}

	return nil
}

func (r *OperatorAgentReconciler) reconcilePVC(ctx context.Context, instance *operatorv1alpha1.OperatorAgent) error {
	pvc := shared.BuildPVC(instance.Name+"-pvc", instance.Namespace, instance.Spec.StorageSize)

	if err := ctrl.SetControllerReference(instance, pvc, r.Scheme); err != nil {
		return err
	}

	found := &corev1.PersistentVolumeClaim{}
	err := r.Get(ctx, client.ObjectKey{Name: pvc.Name, Namespace: pvc.Namespace}, found)
	if err != nil {
		if errors.IsNotFound(err) {
			return r.Create(ctx, pvc)
		}
		return err
	}

	return nil
}

func (r *OperatorAgentReconciler) reconcileDeployment(ctx context.Context, instance *operatorv1alpha1.OperatorAgent) error {
	ksaName := instance.Spec.KSAName
	if ksaName == "" {
		ksaName = instance.Name
	}

	deploy := shared.BuildDeployment(shared.DeploymentConfig{
		Name:                  instance.Name,
		Namespace:             instance.Namespace,
		ImageURI:              instance.Spec.ImageURI,
		Replicas:              instance.Spec.Replicas,
		KSAName:               ksaName,
		ContainerName:         "operator-agent",
		ApiServerKeySecretRef: instance.Spec.ApiServerKeySecretRef,
		ModelName:             instance.Spec.ModelName,
		ModelBaseURL:          instance.Spec.ModelBaseURL,
		ModelAPIKey:           instance.Spec.ModelAPIKey,
		ClusterName:           instance.Spec.ClusterName,
		Location:              instance.Spec.Location,
		ProjectID:             instance.Spec.ProjectID,
		PVCName:               instance.Name + "-pvc",
	})

	if err := ctrl.SetControllerReference(instance, deploy, r.Scheme); err != nil {
		return err
	}

	found := &appsv1.Deployment{}
	err := r.Get(ctx, client.ObjectKey{Name: deploy.Name, Namespace: deploy.Namespace}, found)
	if err != nil {
		if errors.IsNotFound(err) {
			return r.Create(ctx, deploy)
		}
		return err
	}

	if !reflect.DeepEqual(found.Spec, deploy.Spec) || !reflect.DeepEqual(found.Labels, deploy.Labels) {
		found.Spec = deploy.Spec
		found.Labels = deploy.Labels
		return r.Update(ctx, found)
	}
	return nil
}

func (r *OperatorAgentReconciler) reconcileService(ctx context.Context, instance *operatorv1alpha1.OperatorAgent) error {
	svc := shared.BuildService(instance.Name, instance.Namespace)

	if err := ctrl.SetControllerReference(instance, svc, r.Scheme); err != nil {
		return err
	}

	found := &corev1.Service{}
	err := r.Get(ctx, client.ObjectKey{Name: svc.Name, Namespace: svc.Namespace}, found)
	if err != nil {
		if errors.IsNotFound(err) {
			return r.Create(ctx, svc)
		}
		return err
	}

	if !reflect.DeepEqual(found.Spec.Ports, svc.Spec.Ports) || !reflect.DeepEqual(found.Spec.Selector, svc.Spec.Selector) {
		found.Spec.Ports = svc.Spec.Ports
		found.Spec.Selector = svc.Spec.Selector
		return r.Update(ctx, found)
	}
	return nil
}

// SetupWithManager sets up the controller with the Manager.
func (r *OperatorAgentReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&operatorv1alpha1.OperatorAgent{}).
		Owns(&appsv1.Deployment{}).
		Owns(&corev1.PersistentVolumeClaim{}).
		Owns(&corev1.Service{}).
		Owns(&corev1.ServiceAccount{}).
		Named("operatoragent").
		Complete(r)
}
