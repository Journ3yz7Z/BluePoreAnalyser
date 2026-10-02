# Adapted/extracted from Petrobras GeoSlicer, Apache-2.0.
# Source commit c849620433dc36fd1c78cc93f0aa33c32e5b8f54.
# Source: src/modules/Segmenter/BayesianInferenceCLI/BayesianInferenceCLI.py
# Modifications: standalone imports, use only 2D kernel 3/cubes/stride 1 unsafe branch.
# Slicer/VTK IO and FFT large-kernel branch are intentionally not supported.
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

def create_stencil_mask(dim, stencil, kernel_size):
    if dim == 2:
        if stencil == "planes" or stencil == "cubes":
            A = np.ones([kernel_size] * dim, dtype=bool)
        elif stencil == "axes":
            A = np.zeros([kernel_size] * dim, dtype=bool)
            A[kernel_size // 2, :] = 1
            A[:, kernel_size // 2] = 1
    elif dim == 3:
        if stencil == "cubes":
            A = np.ones([kernel_size] * dim, dtype=bool)
        elif stencil == "planes":
            A = np.zeros([kernel_size] * dim, dtype=bool)
            A[kernel_size // 2, :, :] = 1
            A[:, kernel_size // 2, :] = 1
            A[:, :, kernel_size // 2] = 1
        elif stencil == "axes":
            A = np.zeros([kernel_size] * dim, dtype=bool)
            A[kernel_size // 2, kernel_size // 2, :] = 1
            A[:, kernel_size // 2, kernel_size // 2] = 1
            A[kernel_size // 2, :, kernel_size // 2] = 1
    A = A.ravel()

    return A

def bayesian_inference(img, considered_segments, mean, cov, stride, stencil="axes", unsafe_memory_opt=False):
    kernel_size = np.ceil(np.power(cov[0].shape[0], 1.0 / img.ndim)).astype(int)
    kernel_radius = (int(kernel_size - 1) // 2,) * img.ndim

    # use fftconvolve
    if img.ndim == 2 and stride == 1 and kernel_size > 50 and stencil == "cubes":
        logdet = []
        Sigma_inv = []
        for m in range(len(considered_segments)):
            logdet.append(np.linalg.slogdet(cov[m])[1])
            Sigma_inv.append(np.linalg.inv(cov[m]).reshape((kernel_size**2, kernel_size, kernel_size)))

        P = []
        for m in range(len(considered_segments)):
            segment = tuple(annotations[0][annotations[1] == considered_segments[m]].T)
            mu = float(img[segment].mean())

            Pm = np.zeros_like(img)
            for i in range(Sigma_inv[m].shape[0]):
                conv = fftconvolve(img - mu, Sigma_inv[m][i], mode="same")

                img_shift = np.roll(
                    img, (i // kernel_size - kernel_size // 2, i % kernel_size - kernel_size // 2), (0, 1)
                )
                Pm[...] = Pm + (img_shift - mu) * conv

            Pm = -Pm / 2.0 - logdet[m] / 2.0
            P.append(Pm)

        P = np.array(P)
    else:
        A = create_stencil_mask(img.ndim, stencil, kernel_size)

        cov = cov[:, A == True, :][:, :, A == True] + 0.001 * np.eye(sum(A))
        cov_inv = np.array([np.linalg.inv(cov[m]) for m in range(len(considered_segments))])

        logdet = np.array([np.linalg.slogdet(cov[m])[1] for m in range(len(considered_segments))])

        padded_img = np.pad(img, pad_width=(kernel_size - 1) // 2, mode="reflect")

        if unsafe_memory_opt:
            P = np.zeros((len(considered_segments),) + tuple(np.ceil(np.array(img.shape) / stride).astype(int)))
            for m in range(len(considered_segments)):
                w = sliding_window_view(padded_img - mean[m], [kernel_size] * padded_img.ndim)
                if img.ndim == 2:
                    w = w[::stride, ::stride]
                else:
                    w = w[::stride, ::stride, ::stride]

                w = w.reshape(np.prod(w.shape[: img.ndim]), np.prod(w.shape[img.ndim :])).T
                w = w[A == True]

                P[m, ...] = ((-np.sum(w * (cov_inv[m] @ w), axis=0) - logdet[m]) / 2.0).reshape(
                    np.ceil(np.array(img.shape) / stride).astype(int)
                )
        else:
            grid = np.mgrid[[slice(0, img.shape[dim], stride) for dim in range(img.ndim)]]
            points_inf = grid.reshape(grid.shape[0], -1).T
            points_inf_stride = points_inf // stride

            P = np.zeros((len(considered_segments),) + tuple(np.ceil(np.array(img.shape) / stride).astype(int)))
            for m in range(len(considered_segments)):
                w = sliding_window_view(padded_img - mean[m], [kernel_size] * padded_img.ndim)
                for i in range(len(points_inf)):
                    x = w[tuple(points_inf[i])].ravel()
                    x = x[A == True]

                    P[m][tuple(points_inf_stride[i])] = (-np.sum(x * (cov_inv[m] @ x), axis=0) - logdet[m]) / 2.0

    return P
