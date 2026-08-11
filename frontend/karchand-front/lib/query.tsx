import { useQuery, useMutation } from '@tanstack/react-query';


  export const useQueryWrapper = (key:string[], fetchFn:()=>any, options = {}) => {
    return useQuery({
      queryKey: key,
      queryFn: fetchFn,
      ...options,
    });
  }


  export const useMutateWrapper = (mutationFn:()=>any, options = {}) => {
    return useMutation({
      mutationFn,
      ...options,
    });
  }