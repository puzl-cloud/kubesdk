import sys

if sys.version_info >= (3, 13):
    from ._resource_list_pep695 import K8sResourceList, K8sResource
else:
    from ._resource_list_generics import K8sResourceList, K8sResource


__all__ = ["K8sResourceList", "K8sResource"]
