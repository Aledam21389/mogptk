import numpy as np
from .model import model
from .dataset import DataSet
from .kernels import MultiOutputSpectralMixture, Noise
from .plot import plot_spectrum

class MOSM(model):
    """
    Multi Output Spectral Mixture kernel as proposed by our paper.
        
    Args:
        dataset (DataSet): DataSet object of data for all channels.
        Q (int): Number of components to use.
        name (string): Name of the model.
        likelihood (gpflow.likelihoods): Likelihood to use from GPFlow, if None a default exact inference Gaussian likelihood is used.
        variational (bool): If True, use variational inference to approximate function values as Gaussian. If False it will use Monte Carlo Markov Chain (default).
        sparse (bool): If True, will use sparse GP regression. Defaults to False.
        like_params (dict): Parameters to GPflow likelihood.
    """
    def __init__(self, dataset, Q=1, name="MOSM", likelihood=None, variational=False, sparse=False, like_params={}):
        model.__init__(self, name, dataset)
        self.Q = Q

        with self.graph.as_default():
            with self.session.as_default():
                for q in range(Q):
                    kernel = MultiOutputSpectralMixture(
                        self.dataset.get_input_dims()[0],
                        self.dataset.get_output_dims(),
                    )
                    if q == 0:
                        kernel_set = kernel
                    else:
                        kernel_set += kernel
                kernel_set += Noise(self.dataset.get_input_dims()[0], self.dataset.get_output_dims())
                model._build(self, kernel_set, likelihood, variational, sparse, like_params)

    def estimate_params(self, mode='BNSE', sm_init='BNSE', sm_method='BFGS', sm_maxiter=3000, plot=False):
        """
        Estimate kernel parameters.

        The initialization can be done in two ways, the first by estimating the PSD via 
        BNSE (Tobar 2018) and then selecting the greater Q peaks in the estimated spectrum,
        the peaks position, magnitude and width initialize the mean, magnitude and variance
        of the kernel respectively.
        The second way is by fitting independent Gaussian process for each channel, each one
        with SM kernel, using the fitted parameters for initial values of the multioutput kernel.

        In all cases the noise is initialized with 1/30 of the variance 
        of each channel.

        Args:
            mode (str): Method of initializing, possible values are 'BNSE' and SM.
            sm_init (str): Method of initializing SM kernels. Only valid in 'SM' mode.
            sm_method (str): Optimization method for SM kernels. Only valid in 'SM' mode.
            sm_maxiter (str): Maximum iteration for SM kernels. Only valid in 'SM' mode.
            plt (bool): Show the PSD of the kernel after fitting SM kernels.
                Only valid in 'SM' mode. Default to false.
        """

        if mode=='BNSE':
            amplitudes, means, variances = self.dataset.get_bnse_estimation(self.Q)
            for q in range(self.Q):
                magnitude = np.empty((self.dataset.get_output_dims()))
                mean = np.empty((self.dataset.get_input_dims()[0], self.dataset.get_output_dims()))
                variance = np.empty((self.dataset.get_input_dims()[0], self.dataset.get_output_dims()))
                for channel in range(len(self.dataset)):
                    magnitude[channel] = amplitudes[channel, :, q].mean()
                    mean[:, channel] = means[channel, :, q] * 2 * np.pi
                    variance[:, channel] = variances[channel, :, q] * 2
            
                # normalize across channels
                magnitude = np.sqrt(magnitude / magnitude.mean())

                self.set_param(q, 'magnitude', magnitude)
                self.set_param(q, 'mean', mean)
                self.set_param(q, 'variance', variance)

        elif mode=='SM':
            params = _estimate_from_sm(
                self.data,
                self.Q,
                init=sm_init,
                method=sm_method,
                maxiter=sm_maxiter,
                plot=plot)

            for q in range(self.Q):
                self.params[q]["magnitude"] = params[q]['weight'].mean(axis=0)
                self.params[q]["mean"] = params[q]['mean']
                self.params[q]["variance"] = params[q]['scale'] * 2

                self.params[q]["magnitude"] = np.sqrt(self.params[q]["magnitude"] / self.params[q]["magnitude"].mean())
        else:
            raise Exception("possible modes are either 'BNSE' or 'SM'")

        noise = np.empty((self.dataset.get_output_dims()))
        for i, channel in enumerate(self.dataset):
            noise[i] = (channel.Y).var() / 30
        self.set_param(self.Q, 'noise', noise)

    def plot(self):
        names = self.dataset.get_names()
        nyquist = self.dataset.get_nyquist_estimation()

        params = self.get_params()
        means = np.array([params[q]['mean'] for q in range(self.Q)])
        weights = np.array([params[q]['magnitude'] for q in range(self.Q)])**2
        scales = np.array([params[q]['variance'] for q in range(self.Q)])
        plot_spectrum(means, scales, weights=weights, nyquist=nyquist, titles=names)

    def plot_psd(self, figsize=(20, 14), title=''):
        """
        Plot power spectral density and power cross spectral density.

        Note: Implemented only for 1 input dimension.
        """

        cross_params = self.get_cross_params()

        m = self.get_output_dims()

        f, axarr = plt.subplots(m, m, sharex=False, figsize=figsize)

        for i in range(m):
            for j in range(i+1):
                self._plot_power_cross_spectral_density(
                    axarr[i, j],
                    cross_params,
                    channels=(i, j))

        plt.tight_layout()
        return f, axarr

    def _plot_power_cross_spectral_density(self, ax, params, channels=(0, 0)):
        """
        Plot power cross spectral density given axis.

        Args:
            ax (matplotlib.axis): Axis to plot to.
            params(dict): Kernel parameters.
            channels (tuple of ints): Channels to use.
        """
        i = channels[0]
        j = channels[1]

        mean = params['mean'][i, j, 0, :]
        cov = params['covariance'][i, j, 0, :]
        delay = params['delay'][i, j, 0, :]
        magn = params['magnitude'][i, j, :]
        phase = params['phase'][i, j, :]

        
        w_high = (mean + 1* np.sqrt(cov)).max()

        w = np.linspace(-w_high, w_high, 1000)

        # power spectral density
        if i==j:
            psd_total = np.zeros(len(w))
            for q in range(self.Q):
                psd_q = np.exp(-0.5 * (w - mean[q])**2 / cov[q])
                psd_q += np.exp(-0.5 * (w + mean[q])**2 / cov[q])
                psd_q *= magn[q] * 0.5

                ax.plot(w, psd_q, '--r', lw=0.5)

                psd_total += psd_q
            ax.plot(w, psd_total, 'r', lw=2.1, alpha=0.7)
        # power cross spectral density
        else:
            psd_total = np.zeros(len(w)) + 0.j
            for q in range(self.Q):
                psd_q = np.exp(-0.5 * (w - mean[q])**2 / cov[q] + 1.j * (w * delay[q] + phase[q]))
                psd_q += np.exp(-0.5 * (w + mean[q])**2 / cov[q] + 1.j * (w * delay[q] + phase[q]))
                psd_q *= magn[q] * 0.5

                ax.plot(w, np.real(psd_q), '--b', lw=0.5)
                ax.plot(w, np.imag(psd_q), '--g', lw=0.5)
            
                psd_total += psd_q
            ax.plot(w, np.real(psd_total), 'b', lw=1.2, alpha=0.7)
            ax.plot(w, np.imag(psd_total), 'g', lw=1.2, alpha=0.7)
        ax.set_yticks([])
        return

    def plot_correlations(self, figsize=None):
        """
        Plot correlation coeficient matrix.

        This is done evaluating the kernel at K_ij(0, 0)
        for al channels.
        """

        m = self.get_output_dims()

        cross_params = self.get_cross_params()
        mean = cross_params['mean'][:, :, 0, :]
        cov = cross_params['covariance'][:, :, 0, :]
        delay = cross_params['delay'][:, :, 0, :]
        magn = cross_params['magnitude'][:, :, :]
        phase = cross_params['phase'][:, :, :]

        corr_coef_matrix = np.zeros((m, m))

        alpha = magn / np.sqrt(2 * np.pi * cov)

        np.fill_diagonal(corr_coef_matrix, np.diagonal(alpha.sum(2)))

        for i in range(m):
            for j in range(m):
                if i!=j:
                    corr = np.exp(-0.5 *  delay[i, j, :]**2 * cov[i, j, :]) 
                    corr *= np.cos(delay[i, j, :] * mean[i, j, :] + phase[i, j, :])
                    corr_coef_matrix[i, j] = (alpha[i, j, :] * corr).sum()
                norm = np.sqrt(np.diagonal(alpha.sum(2))[i]) * np.sqrt(np.diagonal(alpha.sum(2))[j])
                corr_coef_matrix[i, j] /= norm

        fig, ax = plt.subplots()
        color_range = np.abs(corr_coef_matrix).max()
        norm = mpl.colors.Normalize(vmin=-color_range, vmax=color_range)
        im = ax.matshow(corr_coef_matrix, cmap='coolwarm', norm=norm)
        # fig.colorbar(im,  boundaries=np.linspace(np.round(corr_coef_matrix.min(), 1), np.round(corr_coef_matrix.max(), 1), 7))
        fig.colorbar(im)
        for (i, j), z in np.ndenumerate(corr_coef_matrix):
#             ax.text(j, i, '{:0.1f}'.format(z), ha='center', va='center')
            ax.text(j, i, '{:0.1f}'.format(z), ha='center', va='center', 
                bbox=dict(boxstyle='round', facecolor='white', alpha=0.5, edgecolor='0.9'),
                fontsize=figsize)
        return fig, ax, corr_coef_matrix

    def info(self):
        for channel in range(self.get_output_dims()):
            for q in range(self.Q):
                mean = self.params[q]["mean"][:,channel]
                var = self.params[q]["variance"][:,channel]
                if np.linalg.norm(mean) < np.linalg.norm(var):
                    print("‣ MOSM approaches RBF kernel for Q=%d in channel='%s'" % (q, self.data[channel].name))

    def get_cross_params(self):
        """
        Obtain cross parameters from MOSM

        Returns:
            cross_params(dict): Dictionary with the cross parameters, 'covariance', 'mean',
            'magnitude', 'delay' and 'phase'. Each one a output_dim x output_dim x input_dim x Q
            array with the cross parameters, with the exception of 'magnitude' and 'phase' where 
            the cross parameters are a output_dim x output_dim x Q array.
            NOTE: this assumes the same input dimension for all channels.
        """
        m = self.get_output_dims()
        d = self.get_input_dims()
        Q = self.Q

        cross_params = {}

        cross_params['covariance'] = np.zeros((m, m, d, Q))
        cross_params['mean'] = np.zeros((m, m, d, Q))
        cross_params['magnitude'] = np.zeros((m, m, Q))
        cross_params['delay'] = np.zeros((m, m, d, Q))
        cross_params['phase'] = np.zeros((m, m, Q))

        for q in range(Q):
            for i in range(m):
                for j in range(m):
                    var_i = self.params[q]['variance'][:, i]
                    var_j = self.params[q]['variance'][:, j]
                    mu_i = self.params[q]['mean'][:, i]
                    mu_j = self.params[q]['mean'][:, j]
                    w_i = self.params[q]['magnitude'][i]
                    w_j = self.params[q]['magnitude'][j]
                    sv = var_i + var_j

                    # cross covariance
                    cross_params['covariance'][i, j, :, q] = 2 * (var_i * var_j) / sv
                    # cross mean
                    cross_mean_num = var_i.dot(mu_j) + var_j.dot(mu_i)
                    cross_params['mean'][i, j, :, q] = cross_mean_num / sv
                    # cross magnitude
                    exp_term = -1/4 * ((mu_i - mu_j)**2 / sv).sum()
                    cross_params['magnitude'][i, j, q] = w_i * w_j * np.exp(exp_term)
            # cross phase
            phase_q = self.params[q]['phase']
            cross_params['phase'][:, :, q] = np.subtract.outer(phase_q, phase_q)
            for n in range(d):
                # cross delay        
                delay_n_q = self.params[q]['delay'][n, :]
                cross_params['delay'][:, :, n, q] = np.subtract.outer(delay_n_q, delay_n_q)

        return cross_params