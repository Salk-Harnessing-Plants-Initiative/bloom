const path = require('path');

/** @type {import('next').NextConfig} */
const nextConfig = {
  experimental: {
    serverActions: {},
  },
  images: {
    remotePatterns: [
      {
        protocol: 'http',
        hostname: 'localhost',
        port: '54321',
        pathname: '/storage/v1/render/image/**',
      },
      {
        protocol: 'https',
        hostname: 'api.bloom.salk.edu',
        port: '',
        pathname: '/proxy/storage/v1/render/image/**',
      },
      {
        protocol: 'https',
        hostname: 'api.bloom-staging.salkhpi.org',
        port: '',
        pathname: '/storage/v1/render/image/**',
      },
    ],
  },

  webpack: (config) => {
    config.resolve.fallback = {
      fs: false,
      path: false,
      os: false,
    };

    return config;
  },
};

module.exports = nextConfig;

