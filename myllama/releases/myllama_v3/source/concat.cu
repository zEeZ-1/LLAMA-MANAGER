#include <cstdlib>
#include <cstring>
#include "concat.cuh"

#include <stdint.h>

// contiguous kernels
template <typename T, int dim>
static __global__ void __launch_bounds__(CUDA_CONCAT_BLOCK_SIZE) concat_cont(const T * x,
                                                                             const T * y,
                                                                             T *       dst,
                                                                             int64_t   ne00,
                                                                             int64_t   ne01,
                                                                             int64_t   ne02,
                                                                             int64_t   ne0,
                                                                             int64_t   ne1,
                                                                             int64_t   ne2) {
    static_assert(dim >= 0 && dim <= 2, "dim must be in [0, 2]");

    const int64_t n = ne0 * ne1 * ne2;

    ggml_cuda_pdl_sync();
    for (int64_t i = (int64_t) blockIdx.x * blockDim.x + threadIdx.x; i < n; i += (int64_t) blockDim.x * gridDim.x) {
        if constexpr (dim == 0) {
            const int64_t row = i / ne0;
            const int64_t i0  = i - row * ne0;

            if (i0 < ne00) {
                dst[i] = x[row * ne00 + i0];
            } else {
                dst[i] = y[row * (ne0 - ne00) + (i0 - ne00)];
            }
        } else if constexpr (dim == 1) {
            const int64_t dst_plane  = ne0 * ne1;
            const int64_t src0_plane = ne0 * ne01;
            const int64_t src1_plane = dst_plane - src0_plane;
            const int64_t i2         = i / dst_plane;
            const int64_t i01        = i - i2 * dst_plane;

            if (i01 < src0_plane) {
                dst[i] = x[i2 * src0_plane + i01];
            } else {
                dst[i] = y[i2 * src1_plane + (i01 - src0_plane)];
            }
        } else {
            const int64_t src0_size = ne0 * ne1 * ne02;

            if (i < src0_size) {
                dst[i] = x[i];
            } else {
                dst[i] = y[i - src0_size];
            }
        }
    }
}

template <typename T>
static void concat_cont_cuda(const T * x,
                             const T * y,
                             T *       dst,
                             int64_t   ne00,
                             int64_t   ne01,
                             int64_t   ne02,
                             int64_t   ne0,
                             int64_t   ne1,
                             int64_t   ne2,
                             int       dim,
                             cudaStream_t stream) {
    const int64_t n          = ne0 * ne1 * ne2;
    const int     num_blocks = (n + CUDA_CONCAT_BLOCK_SIZE - 1) / CUDA_CONCAT_BLOCK_SIZE;

    if (dim == 0) {
        const ggml_cuda_kernel_launch_params launch_params = ggml_cuda_kernel_launch_params(num_blocks, CUDA_CONCAT_BLOCK_SIZE, 0, stream);
        ggml_cuda_kernel_launch(concat_cont<T, 0>, launch_params, x, y, dst, ne00, ne01, ne02, ne0, ne1, ne2);
        return;
    }
    if (dim == 1) {
        concat_cont<T, 1><<<num_blocks, CUDA_CONCAT_BLOCK_SIZE, 0, stream>>>(x, y, dst, ne00, ne01, ne02, ne0, ne1, ne2);
        return;
    }
    concat_cont<T, 2><<<num_blocks, CUDA_CONCAT_BLOCK_SIZE, 0, stream>>>(x, y, dst, ne00, ne01, ne02, ne0, ne1, ne2);
}

// non-contiguous kernel (slow)
template <typename T, int dim>
static __global__ void __launch_bounds__(CUDA_CONCAT_BLOCK_SIZE)
    concat_non_cont(
        const char * src0,
        const char * src1,
              char * dst,
           int64_t   ne00,
           int64_t   ne01,
           int64_t   ne02,
           int64_t   ne03,
          uint64_t   nb00,
          uint64_t   nb01,
          uint64_t   nb02,
          uint64_t   nb03,
           int64_t /*ne10*/,
           int64_t /*ne11*/,
           int64_t /*ne12*/,
           int64_t /*ne13*/,
          uint64_t   nb10,
          uint64_t   nb11,
          uint64_t   nb12,
          uint64_t   nb13,
           int64_t   ne0,
           int64_t /*ne1*/,
           int64_t /*ne2*/,
           int64_t /*ne3*/,
          uint64_t   nb0,
          uint64_t   nb1,
          uint64_t   nb2,
          uint64_t   nb3) {
    static_assert(dim >= 0 && dim <= 3, "dim must be in [0, 3]");

    const int64_t i3 = blockIdx.z;
    const int64_t i2 = blockIdx.y;
    const int64_t i1 = blockIdx.x;

    const T * x;

    for (int64_t i0 = threadIdx.x; i0 < ne0; i0 += blockDim.x) {
        if (i0 < ne00 && i1 < ne01 && i2 < ne02 && i3 < ne03) {
            x = (const T *)(src0 + i3*nb03 + i2*nb02 + i1*nb01 + i0*nb00);
        } else {
            if constexpr (dim == 0) {
                x = (const T *)(src1 + i3*nb13 + i2*nb12 + i1*nb11 + (i0 - ne00)*nb10);
            } else if constexpr (dim == 1) {
                x = (const T *)(src1 + i3*nb13 + i2*nb12 + (i1 - ne01)*nb11 + i0*nb10);
            } else if constexpr (dim == 2) {
                x = (const T *)(src1 + i3*nb13 + (i2 - ne02)*nb12 + i1*nb11 + i0*nb10);
            } else if constexpr (dim == 3) {
                x = (const T *)(src1 + (i3 - ne03)*nb13 + i2*nb12 + i1*nb11 + i0*nb10);
            }
        }

        T * y = (T *)(dst + i3*nb3 + i2*nb2 + i1*nb1 + i0*nb0);

        *y = *x;
    }
}

template <typename T>
static void concat_cuda(const ggml_tensor * src0, const ggml_tensor * src1, ggml_tensor * dst, int dim, cudaStream_t stream) {
    if (dim != 3 && ggml_is_contiguous_to_3(src0) && ggml_is_contiguous_to_3(src1)) {
        const T * src0_d = (const T *) src0->data;
        const T * src1_d = (const T *) src1->data;
        T *       dst_d  = (T *) dst->data;

        for (int64_t i3 = 0; i3 < dst->ne[3]; i3++) {
            concat_cont_cuda(
                    src0_d + i3*(src0->nb[3] / sizeof(T)),
                    src1_d + i3*(src1->nb[3] / sizeof(T)),
                    dst_d  + i3*( dst->nb[3] / sizeof(T)),
                    ggml_row_size(src0->type, src0->ne[0])/sizeof(T), src0->ne[1], src0->ne[2],
                    ggml_row_size(dst->type, dst->ne[0])/sizeof(T),  dst->ne[1],  dst->ne[2], dim, stream);
        }
    } else if (dim == 3 && ggml_is_contiguous(src0) && ggml_is_contiguous(src1)) {
        const size_t size0 = ggml_nbytes(src0);
        const size_t size1 = ggml_nbytes(src1);

        CUDA_CHECK(cudaMemcpyAsync((char *) dst->data,         src0->data, size0, cudaMemcpyDeviceToDevice, stream));
        CUDA_CHECK(cudaMemcpyAsync((char *) dst->data + size0, src1->data, size1, cudaMemcpyDeviceToDevice, stream));
    } else {
        GGML_ASSERT(!ggml_is_quantized(src0->type));

        dim3 grid_dim(dst->ne[1], dst->ne[2], dst->ne[3]);
        auto launch_kernel = [&](auto dim) {
            concat_non_cont<T, dim><<<grid_dim, CUDA_CONCAT_BLOCK_SIZE, 0, stream>>>(
                (const char *) src0->data, (const char *) src1->data, (char *) dst->data,
                src0->ne[0], src0->ne[1], src0->ne[2], src0->ne[3],
                src0->nb[0], src0->nb[1], src0->nb[2], src0->nb[3],
                src1->ne[0], src1->ne[1], src1->ne[2], src1->ne[3],
                src1->nb[0], src1->nb[1], src1->nb[2], src1->nb[3],
                dst->ne[0], dst->ne[1], dst->ne[2], dst->ne[3],
                dst->nb[0], dst->nb[1], dst->nb[2], dst->nb[3]);
        };
        switch (dim) {
            case 0:
                launch_kernel(std::integral_constant<int, 0>{});
                break;
            case 1:
                launch_kernel(std::integral_constant<int, 1>{});
                break;
            case 2:
                launch_kernel(std::integral_constant<int, 2>{});
                break;
            case 3:
                launch_kernel(std::integral_constant<int, 3>{});
                break;
            default:
                GGML_ABORT("Invalid dim: %d", dim);
        }
    }
}

namespace {
constexpr int tile = 32;
constexpr int rows = 8;

// QKV is produced token-major. The recurrent convolution consumes channel-major
// rows, each prepended by the three historical timesteps.
__global__ void transpose_prefix(const float * __restrict__ history,
                                 const float * __restrict__ qkv,
                                 float * __restrict__ output, int tokens, int channels) {
    __shared__ float values[tile][tile + 1];
    const int c0 = blockIdx.x * tile;
    const int t0 = blockIdx.y * tile;
    const int x = threadIdx.x, y = threadIdx.y;
    const int stride = tokens + 3;
    #pragma unroll
    for (int i = 0; i < tile; i += rows) {
        const int t = t0+y+i, c = c0+x;
        if (t < tokens && c < channels)
            values[y+i][x] = qkv[size_t(t)*channels+c];
    }
    __syncthreads();
    #pragma unroll
    for (int i = 0; i < tile; i += rows) {
        const int c = c0+y+i, t = t0+x;
        if (c < channels) {
            if (t < tokens) output[size_t(c)*stride+t+3] = values[x][y+i];
            if (t < 3)      output[size_t(c)*stride+t]   = history[size_t(c)*3+t];
        }
    }
}
}
void ggml_cuda_op_concat(ggml_backend_cuda_context & ctx, ggml_tensor * dst) {
    static const bool enabled = [] { const char *v = std::getenv("MYLLAMA_GDN_CONV_TILE"); return v && std::strcmp(v, "1") == 0; }();
    const ggml_tensor *a = dst->src[0], *b = dst->src[1];
    const auto n = b->ne[0], c = b->ne[1];
    if (enabled && dst->op_params[0] == 0 && a->type == GGML_TYPE_F32 && b->type == GGML_TYPE_F32 &&
        dst->type == GGML_TYPE_F32 && n >= 32 && n < (1<<20) && c > 0 && c < (1<<21) &&
        a->ne[0] == 3 && a->ne[1] == c &&
        a->ne[2] == 1 && a->ne[3] == 1 && b->ne[2] == 1 && b->ne[3] == 1 &&
        a->nb[0] == sizeof(float) && a->nb[1] == 3*sizeof(float) &&
        b->nb[0] == c*sizeof(float) && b->nb[1] == sizeof(float) &&
        dst->nb[0] == sizeof(float) && dst->nb[1] == (n+3)*sizeof(float)) {
        transpose_prefix<<<dim3((c+tile-1)/tile,(n+tile-1)/tile),dim3(tile,rows),0,ctx.stream()>>>(
            static_cast<const float *>(a->data), static_cast<const float *>(b->data),
            static_cast<float *>(dst->data), int(n), int(c));
        return;
    }

    const ggml_tensor * src0 = dst->src[0];
    const ggml_tensor * src1 = dst->src[1];

    cudaStream_t stream = ctx.stream();

    const int32_t dim = ((int32_t *) dst->op_params)[0];

    GGML_ASSERT(src0->type == src1->type);
    GGML_ASSERT(dst->type  == src0->type);

    if (ggml_is_quantized(src0->type)) {
        if (dim == 3) {
            GGML_ASSERT(ggml_is_contiguous(src0));
            GGML_ASSERT(ggml_is_contiguous(src1));
        } else {
            GGML_ASSERT(ggml_is_contiguous_to_3(src0));
            GGML_ASSERT(ggml_is_contiguous_to_3(src1));
        }
        GGML_ASSERT(src0->ne[0] % ggml_blck_size(src0->type) == 0);
        GGML_ASSERT(src1->ne[0] % ggml_blck_size(src1->type) == 0);

        // if first 3 dimensions are contiguous and ne[0] is multiple of the block size we can concat both tensors as byte tensors
        concat_cuda<uint8_t>(src0, src1, dst, dim, stream);
    } else {
        GGML_ASSERT(ggml_blck_size(src0->type) == 1);

        switch (ggml_type_size(src0->type)) {
            case 1:
                concat_cuda<uint8_t>(src0, src1, dst, dim, stream);
                break;
            case 2:
                concat_cuda<uint16_t>(src0, src1, dst, dim, stream);
                break;
            case 4:
                concat_cuda<uint32_t>(src0, src1, dst, dim, stream);
                break;
            case 8:
                concat_cuda<uint64_t>(src0, src1, dst, dim, stream);
                break;
            default:
                GGML_ABORT("Unsupported type size: %zu", ggml_type_size(src0->type));
        }
    }
}
