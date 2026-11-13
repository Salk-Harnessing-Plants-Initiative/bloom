const path = require('path');

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Turbopack config for Next.js 16
  turbopack: {
    // Disable HMR for problematic MUI CSS modules
    moduleIdStrategy: 'deterministic',
  },
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
  webpack: (config, { isServer, dev }) => {
    // Exclude the MUI data grid from server-side rendering
    if (isServer) {
      config.externals = [
        ...(config.externals || []),
        '@mui/x-data-grid',
      ];
    }
    
    // In development, exclude CSS from node_modules from HMR to prevent module factory errors
    if (dev && !isServer) {
      config.watchOptions = {
        ...config.watchOptions,
        ignored: [
          '**/node_modules/@mui/x-data-grid/**/*.css',
          ...(Array.isArray(config.watchOptions?.ignored) 
            ? config.watchOptions.ignored 
            : [config.watchOptions?.ignored].filter(Boolean)
          ),
        ],
      };
    }
    
    return config;
  },
};

module.exports = nextConfig;
