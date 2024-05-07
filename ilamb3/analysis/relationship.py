"""ILAMB Analysis assets for studying relationships between variables.

There is a lot of data to manage for relationships. So we build a relationship class
and then later create a ILAMBAnalysis ABC which uses it in an ILAMB analysis.

"""

from dataclasses import dataclass, field
from typing import Union

import numpy as np
import pandas as pd
import xarray as xr

from ilamb3 import compare as cmp
from ilamb3.analysis.base import ILAMBAnalysis
from ilamb3.regions import Regions


@dataclass
class Relationship:
    """A class for developing and comparing relationships from gridded data."""

    dep: xr.DataArray
    ind: xr.DataArray
    color: xr.DataArray = None
    dep_log: bool = False
    ind_log: bool = False
    dep_label: str = ""
    ind_label: str = ""
    order: int = 1
    _dep_limits: list[float] = field(init=False, default_factory=lambda: None)
    _ind_limits: list[float] = field(init=False, default_factory=lambda: None)
    _dist2d: np.ndarray = field(init=False, default_factory=lambda: None)
    _ind_edges: np.ndarray = field(init=False, default_factory=lambda: None)
    _dep_edges: np.ndarray = field(init=False, default_factory=lambda: None)
    _response_mean: np.ndarray = field(init=False, default_factory=lambda: None)
    _response_std: np.ndarray = field(init=False, default_factory=lambda: None)

    def __post_init__(self):
        # check input dataarrays for compatibility
        assert isinstance(self.dep, xr.DataArray)
        assert isinstance(self.ind, xr.DataArray)
        self.dep = self.dep.sortby(list(self.dep.sizes.keys()))
        self.ind = self.ind.sortby(list(self.ind.sizes.keys()))
        self.dep, self.ind = xr.align(self.dep, self.ind, join="exact")
        if self.color is not None:
            assert isinstance(self.color, xr.DataArray)
            self.color = self.color.sortby(list(self.color.sizes.keys()))
            self.dep, self.ind, self.color = xr.align(
                self.dep, self.ind, self.color, join="exact"
            )

        # only consider where both are valid and finite
        keep = self.dep.notnull() * self.ind.notnull()
        keep *= np.isfinite(self.dep)
        keep *= np.isfinite(self.ind)
        self.dep = xr.where(keep, self.dep, np.nan)
        self.ind = xr.where(keep, self.ind, np.nan)
        if self.dep_log:
            assert self.dep.min() > 0
        if self.ind_log:
            assert self.ind.min() > 0

    def compute_limits(
        self, rel: Union["Relationship", None] = None
    ) -> Union["Relationship", None]:
        """Compute the limits of the dependent and independent variables.

        Parameters
        ----------

        Returns
        -------

        """

        def _singlelimit(var, limit=None):
            lim = [var.min(), var.max()]
            delta = 1e-8 * (lim[1] - lim[0])
            lim[0] -= delta
            lim[1] += delta
            if limit is None:
                limit = lim
            else:
                limit[0] = min(limit[0], lim[0])
                limit[1] = max(limit[1], lim[1])
            return limit

        dep_lim = _singlelimit(self.dep)
        ind_lim = _singlelimit(self.ind)
        if rel is not None:
            dep_lim = _singlelimit(self.dep, limit=dep_lim)
            ind_lim = _singlelimit(self.ind, limit=ind_lim)
            rel._dep_limits = dep_lim
            rel._ind_limits = ind_lim
        self._dep_limits = dep_lim
        self._ind_limits = ind_lim
        return rel

    def build_response(self, nbin: int = 25, eps: float = 3e-3):
        """Creates a 2D distribution and a functional response

        Parameters
        ----------
        nbin
            the number of bins to use in both dimensions
        eps
            the fraction of points required for a bin in the
            independent variable be included in the funcitonal responses
        """
        # if no limits have been created, make them now
        if self._dep_limits is None or self._ind_limits is None:
            self._dep_limits, self._ind_limits = self.compute_limits(
                dep_lim=self._dep_limits,
                ind_lim=self._ind_limits,
            )

        # compute the 2d distribution
        ind = np.ma.masked_invalid(self.ind.values).compressed()
        dep = np.ma.masked_invalid(self.dep.values).compressed()
        xedges = nbin
        yedges = nbin
        if self.ind_log:
            xedges = 10 ** np.linspace(
                np.log10(self.ind_limits[0]), np.log10(self.ind_limits[1]), nbin + 1
            )
        if self.dep_log:
            yedges = 10 ** np.linspace(
                np.log10(self.dep_limits[0]), np.log10(self.dep_limits[1]), nbin + 1
            )
        dist, xedges, yedges = np.histogram2d(
            ind, dep, bins=[xedges, yedges], range=[self._ind_limits, self._dep_limits]
        )
        dist = np.ma.masked_values(dist.T, 0).astype(float)
        dist /= dist.sum()
        self._dist2d = dist
        self._ind_edges = xedges
        self._dep_edges = yedges

        # compute a binned functional response
        which_bin = np.digitize(ind, xedges).clip(1, xedges.size - 1) - 1
        mean = np.ma.zeros(xedges.size - 1)
        std = np.ma.zeros(xedges.size - 1)
        cnt = np.ma.zeros(xedges.size - 1)
        with np.errstate(under="ignore"):
            for i in range(mean.size):
                depi = dep[which_bin == i]
                cnt[i] = depi.size
                if cnt[i] == 0:  # will get masked out later
                    mean[i] = 0
                    std[i] = 0
                else:
                    if self.dep_log:
                        depi = np.log10(depi)
                        mean[i] = 10 ** depi.mean()
                        std[i] = 10 ** depi.std()
                    else:
                        mean[i] = depi.mean()
                        std[i] = depi.std()
            mean = np.ma.masked_array(mean, mask=(cnt / cnt.sum()) < eps)
            std = np.ma.masked_array(std, mask=(cnt / cnt.sum()) < eps)
        self._response_mean = mean
        self._response_std = std

    def score_response(self, rel: "Relationship") -> float:
        """Score the reponse using the relative RMSE error."""
        rel_error = np.linalg.norm(
            self._response_mean - rel._response_mean
        ) / np.linalg.norm(self._response_mean)
        score = np.exp(-rel_error)
        return score


class relationship_analysis(ILAMBAnalysis):
    def __init__(self, dep_variable: str, ind_variable: str):
        self.dep_variable = dep_variable
        self.ind_variable = ind_variable

    def required_variables(self) -> list[str]:
        return [self.dep_variable, self.ind_variable]

    def __call__(
        self,
        ref: xr.Dataset,
        com: xr.Dataset,
        regions: list[Union[str, None]] = [None],
        **kwargs,
    ) -> tuple[pd.DataFrame, xr.Dataset, xr.Dataset]:
        # Initialize
        analysis_name = "Relationship"
        var_ind = self.ind_variable
        var_dep = self.dep_variable
        for var in self.required_variables():
            ref, com = cmp.make_comparable(ref, com, var)
        ref = ref.pint.dequantify()
        com = com.pint.dequantify()
        ilamb_regions = Regions()
        dfs = []
        for region in regions:
            refr = ilamb_regions.restrict_to_region(ref, region)
            comr = ilamb_regions.restrict_to_region(com, region)
            rel_ref = Relationship(refr[var_dep], refr[var_ind])
            rel_com = Relationship(comr[var_dep], comr[var_ind])
            rel_com = rel_ref.compute_limits(rel_com)
            rel_ref.build_response()
            rel_com.build_response()
            score = rel_ref.score_response(rel_com)
            dfs.append(
                [
                    "Comparison",
                    str(region),
                    analysis_name,
                    f"Score {var_dep} vs {var_ind}",
                    "score",
                    "",
                    score,
                ]
            )

        # Convert to dataframe
        dfs = pd.DataFrame(
            dfs,
            columns=[
                "source",
                "region",
                "analysis",
                "name",
                "type",
                "units",
                "value",
            ],
        )
        return dfs, None, None
